"""Konuşma tanıma: Google (çevrimiçi) ve faster-whisper (çevrimdışı).

AIR_CANVAS_STT            ``auto`` (varsayılan): önce Google, olmazsa Whisper
                          ``google``: yalnızca Google · ``whisper``: yalnızca çevrimdışı Whisper
AIR_CANVAS_WHISPER_MODEL  Whisper modeli: ``tiny``/``base``/``small``/``medium`` veya yerel klasör
                          yolu (varsayılan: ``small`` — Türkçe için hız/doğruluk dengesi)

Whisper için: ``pip install "air-canvas[offline]"``. Model ilk kullanımda bir kez indirilir;
sonra internet gerekmez. Ses kaydı her iki motorda da SpeechRecognition + PyAudio ile yapılır.
"""

import logging
import os
import threading
from typing import List, Optional

log = logging.getLogger(__name__)

STT_ENGINES = ("auto", "google", "whisper")
DEFAULT_WHISPER_MODEL = "small"

# Whisper sessizlikte/gürültüde bu tür altyazı kalıpları "uydurur"; komut sanılmamalı.
_WHISPER_HALLUCINATIONS = (
    "altyazı", "altyazi", "abone ol", "izlediğiniz için", "izlediginiz icin",
    "beğenmeyi unutmayın", "begenmeyi unutmayin", "www.", ".com",
)


class ListenError(Exception):
    """Kullanıcıya söylenecek Türkçe açıklamayı taşır."""

    def __init__(self, message: str, kind: str = "error"):
        super().__init__(message)
        self.message = message
        self.kind = kind          # "missing" | "mic" | "timeout" | "unclear" | "offline" | "error"


class _EngineUnavailable(Exception):
    pass


class SpeechListener:
    def __init__(self, engine: Optional[str] = None, language: str = "tr"):
        engine = (engine or os.getenv("AIR_CANVAS_STT") or "auto").strip().lower()
        self.engine = engine if engine in STT_ENGINES else "auto"
        self.language = language
        self.whisper_model_name = os.getenv("AIR_CANVAS_WHISPER_MODEL") or DEFAULT_WHISPER_MODEL
        self._whisper = None
        self._whisper_lock = threading.Lock()
        self._whisper_failed = ""

    # ─── Kayıt ────────────────────────────────────────────────────────────────

    def listen(self, timeout: float = 5, phrase_time_limit: float = 12) -> str:
        """Mikrofonu dinler ve metni döndürür; olmazsa ``ListenError`` fırlatır."""
        try:
            import speech_recognition as sr  # type: ignore
        except ImportError:
            raise ListenError("Ses tanıma paketi kurulu değil. Kurmak için: pip install air-canvas[voice]",
                              "missing")
        recognizer = sr.Recognizer()
        try:
            with sr.Microphone() as source:
                recognizer.adjust_for_ambient_noise(source, duration=0.3)
                audio = recognizer.listen(source, timeout=timeout, phrase_time_limit=phrase_time_limit)
        except sr.WaitTimeoutError:
            raise ListenError("Konuşma duymadım. V tuşuna basıp hemen konuşun.", "timeout")
        except (OSError, AttributeError, ImportError) as exc:
            # PyAudio yoksa AttributeError/ImportError, mikrofon yoksa OSError gelir.
            log.debug("[AirCanvas] Mikrofon açılamadı: %s", exc)
            raise ListenError("Mikrofon açılamadı. Mikrofonun bağlı olduğundan ve PyAudio'nun kurulu "
                              "olduğundan emin olun.", "mic")
        return self.transcribe(audio, recognizer)

    # ─── Tanıma ───────────────────────────────────────────────────────────────

    def _order(self) -> List[str]:
        return {"google": ["google"], "whisper": ["whisper"]}.get(self.engine, ["google", "whisper"])

    def transcribe(self, audio, recognizer=None) -> str:
        reasons: List[str] = []
        unclear = False
        for name in self._order():
            try:
                text = self._google(audio, recognizer) if name == "google" else self._whisper_text(audio)
            except _EngineUnavailable as exc:
                log.debug("[AirCanvas] %s kullanılamadı: %s", name, exc)
                reasons.append(str(exc))
                continue
            if text:
                return text
            unclear = True
            break   # Motor çalıştı ama konuşma anlaşılamadı; diğerini denemek yalnızca gecikme ekler.
        if unclear:
            raise ListenError("Söylediğinizi anlayamadım, lütfen tekrar söyleyin.", "unclear")
        raise ListenError(" ".join(reasons) or "Ses tanıma kullanılamıyor.", "offline")

    def _google(self, audio, recognizer=None) -> str:
        import speech_recognition as sr  # type: ignore

        recognizer = recognizer or sr.Recognizer()
        # recognize_google çalışma anında eklenir; statik analiz göremediği için getattr.
        recognize = getattr(recognizer, "recognize_google", None)
        if recognize is None:
            raise _EngineUnavailable("Bu SpeechRecognition sürümünde Google tanıma yok.")
        try:
            return (recognize(audio, language="tr-TR") or "").strip()
        except sr.UnknownValueError:
            return ""
        except sr.RequestError as exc:
            raise _EngineUnavailable(f"İnternet yok ya da Google ses tanımaya ulaşılamadı ({exc}).")

    # ─── Whisper (çevrimdışı) ─────────────────────────────────────────────────

    def _load_whisper(self):
        with self._whisper_lock:
            if self._whisper is not None:
                return self._whisper
            if self._whisper_failed:
                raise _EngineUnavailable(self._whisper_failed)
            try:
                from faster_whisper import WhisperModel  # type: ignore
            except ImportError:
                self._whisper_failed = ("Çevrimdışı ses tanıma kurulu değil. Kurmak için: "
                                        "pip install air-canvas[offline]")
                raise _EngineUnavailable(self._whisper_failed)
            try:
                log.info("[AirCanvas] Whisper modeli yükleniyor: %s", self.whisper_model_name)
                self._whisper = WhisperModel(self.whisper_model_name, device="cpu", compute_type="int8")
            except Exception as exc:
                # Kalıcı olarak işaretleme: model ilk seferde indirilemediyse sonra tekrar denenebilir.
                raise _EngineUnavailable(f"Whisper modeli yüklenemedi ({exc}). Model ilk kullanımda bir kez "
                                         "indirilir; internete bağlıyken bir kez deneyin.")
            return self._whisper

    def preload_async(self) -> None:
        """Çevrimdışı modda modeli arka planda önceden yükler (ilk komut gecikmesin)."""
        if "whisper" not in self._order():
            return

        def _load():
            try:
                self._load_whisper()
            except _EngineUnavailable as exc:
                log.warning("[AirCanvas] %s", exc)

        threading.Thread(target=_load, daemon=True, name="AirCanvasWhisperLoad").start()

    def _whisper_text(self, audio) -> str:
        import numpy as np

        model = self._load_whisper()
        raw = audio.get_raw_data(convert_rate=16000, convert_width=2)
        samples = np.frombuffer(raw, np.int16).astype(np.float32) / 32768.0
        segments, _ = model.transcribe(samples, language=self.language, beam_size=5, vad_filter=True,
                                       condition_on_previous_text=False)
        return filter_hallucination(" ".join(seg.text.strip() for seg in segments))


def filter_hallucination(text: str) -> str:
    """Whisper'ın sessizlikte uydurduğu altyazı kalıplarını boş metne çevirir."""
    text = (text or "").strip()
    low = text.replace("İ", "i").replace("I", "ı").lower()
    if not text or any(h in low for h in _WHISPER_HALLUCINATIONS):
        return ""
    return text
