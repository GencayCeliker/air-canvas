"""
air_canvas.canvas — Air Canvas (Elle Havada Çizim + Sesli Asistan)

Kamera üzerinden el hareketleriyle (veya fareyle) çizim yapar.

Hareketler:
    - Tek parmak (işaret)            → çizim
    - İki parmak (işaret + orta)     → seçim / üst menü butonları
    - Açık el (dört parmak)          → fırça kalınlığı (başparmak–işaret mesafesi)

Tasarım notları:
    - Tüm tuval değişiklikleri kamera döngüsünün thread'inde yapılır. Sesli komutlar
      thread-safe bir kuyruğa eylem olarak eklenir ve döngüde uygulanır.
    - Her çizgi (fırça, silgi, şekil) tek bir geri alma adımıdır; geçmiş UNDO_LIMIT ile sınırlıdır.
    - Şekil düzeltme, çizgi öncesi tuval görüntüsüne geri dönüp şekli çizer; diğer çizimlere dokunmaz.
"""

import logging
import os
import queue
import re
import threading
import time
import urllib.request
from collections import deque
from typing import Any, Callable, Dict, List, Optional, Tuple

import cv2
import numpy as np

if __package__ in (None, ""):
    # Dosya doğrudan çalıştırıldı (IDE'de "Run Python File" veya ``python air_canvas/canvas.py``);
    # göreli import'lar çalışmaz. Paket kökünü ekleyip normal giriş noktasına devret.
    import sys
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    from air_canvas.__main__ import main as _main
    sys.exit(_main())

from .config import default_model_path, default_save_dir
from .textutil import clean_for_speech, clean_markdown

try:
    import mediapipe as mp
    MP_OK = hasattr(mp, 'solutions')
except ImportError:
    mp = None
    MP_OK = False

# Yeni mediapipe sürümleri eski `mp.solutions` API'sini kaldırdı; Tasks API ile devam edilir.
try:
    from mediapipe.tasks import python as mp_python
    from mediapipe.tasks.python import vision as mp_vision
    MP_TASKS_OK = mp is not None
except Exception:
    mp_python = None
    mp_vision = None
    MP_TASKS_OK = False

log = logging.getLogger(__name__)

# MediaPipe 21 el eklemi bağlantı haritası (iskelet çizimi için)
HAND_CONNECTIONS = [
    (0, 1), (1, 2), (2, 3), (3, 4),        # Başparmak
    (0, 5), (5, 6), (6, 7), (7, 8),        # İşaret parmağı
    (5, 9), (9, 10), (10, 11), (11, 12),   # Orta parmak
    (9, 13), (13, 14), (14, 15), (15, 16), # Yüzük parmağı
    (13, 17), (0, 17), (17, 18), (18, 19), (19, 20), # Serçe parmak ve avuç
]

HAND_MODEL_URL = (
    "https://storage.googleapis.com/mediapipe-models/"
    "hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task"
)

# ─── Yerleşim ve davranış sabitleri ────────────────────────────────────────────
FRAME_W, FRAME_H = 1280, 720
TOOLBAR_TOP, TOOLBAR_BOTTOM = 10, 95
DRAW_MIN_Y = 105            # Bu çizginin üstü menü alanıdır, çizim yapılmaz
HUD_TOP = 610
UNDO_LIMIT = 25             # Her adım ~2.7 MB; geçmiş sınırlı tutulur
HAND_LOST_GRACE_FRAMES = 6  # El birkaç kare kaybolursa çizgi kopmaz
MIN_STROKE_POINTS = 15
BRUSH_MIN, BRUSH_MAX, BRUSH_DEFAULT, BRUSH_STEP = 4, 40, 15, 3
SMOOTHING = 0.35            # Titreme azaltma (önceki noktanın ağırlığı)
BOARD_BG = (32, 28, 28)     # Tahta modu arka planı (BGR)

# 3D tasarım rehberi: solda yarı saydam model, sağda adım paneli (x1, y1, x2, y2)
GUIDE_RECT = (20, DRAW_MIN_Y + 40, 860, HUD_TOP - 8)
PANEL_RECT = (872, DRAW_MIN_Y + 40, FRAME_W - 10, HUD_TOP - 8)
GUIDE_ALPHA = 0.55

# Tasarım modu sesli komutları
QUESTION_WORDS = {"nasıl", "nasil", "neden", "niye", "niçin", "hangi", "kaç", "kac", "nedir", "ne",
                  "mı", "mi", "mu", "mü", "nerede", "nereye"}

PALETTE: List[Tuple[str, Tuple[int, int, int]]] = [
    ("MAVI", (255, 0, 0)),
    ("YESIL", (0, 255, 0)),
    ("KIRMIZI", (0, 0, 255)),
    ("SARI", (0, 255, 255)),
    ("MOR", (255, 0, 255)),
    ("BEYAZ", (255, 255, 255)),
]

TOOLBAR_IDS = [f"color_{i}" for i in range(len(PALETTE))] + [
    "eraser", "shape", "board", "clear", "undo", "save", "voice",
]

# El hareketiyle basılan butonlarda tekrar tetiklenmeyi önleyen bekleme süreleri (sn)
BUTTON_COOLDOWNS = {"shape": 0.8, "board": 0.8, "clear": 0.8, "undo": 0.6, "save": 1.0, "voice": 1.5}

VOICE_COLORS = [
    (("mavi",), 0),
    (("yeşil", "yesil"), 1),
    (("kırmızı", "kirmizi"), 2),
    (("sarı", "sari"), 3),
    (("mor",), 4),
    (("beyaz",), 5),
]

# Ekranda ASCII etiketler kullanılır (OpenCV Türkçe harf çizemez);
# sesli okumada doğru Türkçe yazımlar kullanılır.
SPOKEN_NAMES = {
    "MAVI": "mavi", "YESIL": "yeşil", "KIRMIZI": "kırmızı", "SARI": "sarı",
    "MOR": "mor", "BEYAZ": "beyaz",
    "DUZ CIZGI": "düz çizgi", "UCGEN": "üçgen", "KARE": "kare",
    "DIKDORTGEN": "dikdörtgen", "DAIRE": "daire", "ELIPS": "elips",
    "ALTIGEN": "altıgen", "YILDIZ": "yıldız",
}


def _spoken(label: str) -> str:
    return SPOKEN_NAMES.get(label, label.lower())


# Sıra önemlidir: önce daha özel kelimeler ("dikdörtgen" → "kare"den önce).
VOICE_SHAPES = [
    (("dikdörtgen", "dikdortgen"), "DIKDORTGEN"),
    (("daire", "çember", "cember", "yuvarlak"), "DAIRE"),
    (("elips", "oval"), "ELIPS"),
    (("kare", "kutu"), "KARE"),
    (("üçgen", "ucgen"), "UCGEN"),
    (("altıgen", "altigen"), "ALTIGEN"),
    (("yıldız", "yildiz"), "YILDIZ"),
    (("çizgi", "cizgi"), "DUZ CIZGI"),
]

# Sesli şekil boyutu ve konumu (kelime kökleri; "alt" ile "altıgen" karışmasın diye tam ekler)
VOICE_SIZES = [(("büyük", "buyuk", "kocaman", "dev"), 1.6, "büyük"),
               (("küçük", "kucuk", "minik", "ufak"), 0.55, "küçük")]


def _forms(*stems: str) -> set:
    suffixes = ("", "a", "e", "da", "de", "ta", "te", "daki", "deki", "taki", "teki", "ya", "ye",
                "ında", "inde", "unda", "ünde", "ına", "ine", "una", "üne")
    return {stem + suf for stem in stems for suf in suffixes}


VOICE_PLACES = {
    "left": _forms("sol"),
    "right": _forms("sağ", "sag"),
    "up": _forms("üst", "ust", "yukarı", "yukari"),
    "down": _forms("alt", "aşağı", "asagi"),
    "center": _forms("orta", "merkez"),
}
STAMP_RADIUS = 130


class AirCanvas:
    def __init__(
        self,
        auto_init: bool = True,
        speaker=None,
        llm=None,
        save_dir: Optional[str] = None,
        camera_index: int = 0,
        model_path: Optional[str] = None,
        assistant=None,
        listener=None,
        ai_mode: Optional[str] = None,
        offline: Optional[bool] = None,
    ):
        """
        speaker: ``say(text)`` ve ``is_busy()`` metotları olan bir ses motoru.
                 Verilmezse ilk ihtiyaçta yerleşik Türkçe motor (Edge TTS → gTTS) kurulur.
        llm:     ``generate(prompt, system=..., temperature=..., max_tokens=...)`` ve
                 ``available`` özelliği olan bir istemci. Verilmezse LM Studio / Ollama denenir.
        assistant: ``design(text)``, ``ask(...)``, ``review(...)`` metotları olan tasarım asistanı.
                 Verilmezse ilk ihtiyaçta ``ai.DesignAssistant`` kurulur (Claude → yerel LLM → şablon).
        listener: metni döndüren (olmazsa ``listen.ListenError`` fırlatan) ``listen()`` metodu olan
                 konuşma tanıyıcı. Verilmezse ``listen.SpeechListener`` (Google → Whisper) kurulur.
        ai_mode: ``auto`` / ``claude`` / ``local`` / ``template`` (bkz. ``ai.py``).
        offline: True ise hiçbir veri internete gitmez: yerel LLM, çevrimdışı Whisper ve
                 yerel Windows sesi kullanılır (``AIR_CANVAS_OFFLINE=1`` ile de açılır).
        """
        self.available = False
        self.detector = None
        self._video_mode = False
        self._last_ts_ms = 0
        self.mp_hands = None
        self.hands = None

        # Kullanıcıya açık durum
        self.shape_assist = True     # Geometrik şekil düzeltme modu
        self.board_mode = False      # False: kamera üstüne çizim, True: düz tahta
        self.show_hud = True
        self.voice_feedback = True   # Sesli geri bildirim (testlerde kapatılır)
        self.voice_listening = False
        self.voice_raw = ""
        self.voice_summary = "Sesli not icin 'V' tusuna basin veya SESLI butonuna iki parmakla dokunun."
        self.voice_notes: List[str] = []
        self.assistant_tip = "Asistan hazir: Tek parmak ciz, iki parmak sec, acik el kalinlik."
        self.colors = [color for _, color in PALETTE]
        self.color_index = 0
        self.eraser = False
        self.brush_thickness = BRUSH_DEFAULT
        self.last_saved_path = ""

        # Tuval ve geçmiş
        self.canvas = np.zeros((FRAME_H, FRAME_W, 3), np.uint8)
        self.undo_stack: deque = deque(maxlen=UNDO_LIMIT)
        self.redo_stack: deque = deque(maxlen=UNDO_LIMIT)

        # Aktif çizgi durumu
        self._stroke_owner: Optional[str] = None   # "hand" | "mouse" | None
        self._stroke_base: Optional[np.ndarray] = None
        self._stroke_points: List[Tuple[int, int]] = []
        self._stroke_dirty = False
        self._last_point: Optional[Tuple[int, int]] = None
        self._missing_frames = 0
        self._hand_overlay: Optional[Tuple[List[List[int]], str]] = None

        # Thread'ler arası iletişim ve yaşam döngüsü
        self._actions: "queue.Queue[Tuple[str, Any]]" = queue.Queue()
        self._cooldowns: Dict[str, float] = {}
        self._speech_lock = threading.Lock()
        self._speaker = speaker          # Dışarıdan verilen ses motoru (isteğe bağlı)
        self._speaker_failed = False
        # Sırayla okunması gereken mesajlar (tasarım adımları, AI cevapları): (nesil, metin)
        self._speech_queue: "queue.Queue[Tuple[int, str]]" = queue.Queue()
        self._speech_gen = 0             # stop_speech() bu sayacı artırır; eski mesajlar atlanır
        self._speech_worker: Optional[threading.Thread] = None
        self._run_lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._llm = llm
        self._llm_checked_at = 0.0
        self._listener = listener        # listen() metodu olan konuşma tanıyıcı (isteğe bağlı)
        self._last_view: Optional[np.ndarray] = None
        self._last_shape_label = ""
        self._last_shape_time = 0.0
        self._session_saves = 0

        # Pencere ve 3D tasarım asistanı
        self.fullscreen = True
        self._window_dirty = False
        self.design = None                 # design.Design
        self.design_step = 0               # -1: genel görünüm, 0..n-1: parça adımı
        self.show_guide = True
        self.design_busy = False
        self.ai_busy = False
        self.last_export_dir = ""
        self._guide: Optional[Tuple[np.ndarray, np.ndarray]] = None   # (BGR, maske)
        self._assistant = assistant
        self._pending_design = ""
        self._answer_text = ""             # Uzun AI cevabı için ekrandaki kutu
        self._answer_until = 0.0
        self._clarify_request = ""         # AI ölçü sorduysa cevabı beklenen tasarım isteği
        self._clarify_until = 0.0

        # Çevrimdışı mod: yerel LLM + Whisper + yerel ses; Claude, Google ve Edge TTS kullanılmaz.
        if offline is None:
            offline = os.getenv("AIR_CANVAS_OFFLINE", "").strip().lower() in ("1", "true", "yes", "evet")
        self.offline = bool(offline)
        self.ai_mode = (ai_mode or ("local" if self.offline else os.getenv("AIR_CANVAS_AI") or "auto")).lower()
        if self.offline and self.ai_mode in ("auto", "claude"):
            self.ai_mode = "local"

        self.buttons = self._build_toolbar()

        self.save_dir = save_dir or default_save_dir()
        self.camera_index = camera_index
        self.model_path = model_path

        if auto_init:
            self._try_init()

    # ─── Yaşam döngüsü ────────────────────────────────────────────────────────

    @property
    def is_running(self) -> bool:
        return self._running

    def stop(self) -> None:
        """Açık çizim penceresini kapatır (uygulama kapanışında çağrılabilir)."""
        self._stop_event.set()

    def queue_design(self, request: str) -> None:
        """Çizim penceresi açılınca hazırlanacak bir 3D tasarım isteği bırakır."""
        self._pending_design = (request or "").strip()

    def start_async(self, on_finish: Optional[Callable[[str], None]] = None) -> str:
        """
        Çizim döngüsünü arka plan thread'inde başlatır ve hemen döner; böylece çağıran
        uygulama çalışmaya devam eder. Pencere kapanınca on_finish(sonuç_mesajı) çağrılır.
        """
        if self._running:
            return "Air Canvas zaten açık. Kapatmak için pencerede Q tuşuna basın."
        if not self.available:
            self._try_init()
        if not self.available:
            return self._unavailable_message()

        def _worker():
            result = self.run()
            if on_finish:
                try:
                    on_finish(result)
                except Exception as exc:
                    log.debug("[AirCanvas] on_finish geri çağrısı hata verdi: %s", exc)

        threading.Thread(target=_worker, daemon=True, name="AirCanvas").start()
        return ("Air Canvas açıldı. Tek parmakla çizin, iki parmakla menüden seçin, "
                "açık elle fırça kalınlığını ayarlayın. Çıkmak için Q.")

    @staticmethod
    def _unavailable_message() -> str:
        log.warning("[SYSTEM_HEALTH][MISSING_PKG] mediapipe el algılama kullanılamıyor. Sanal tuval (AirCanvas) devre dışı bırakıldı.")
        return "Air Canvas için MediaPipe gerekiyor: pip install mediapipe"

    # ─── Yardımcılar ──────────────────────────────────────────────────────────

    @staticmethod
    def _tr_ascii(text: str) -> str:
        """OpenCV putText için Türkçe karakterleri okunaklı ASCII karşılıklarına dönüştürür."""
        tr_map = str.maketrans({
            "ç": "c", "Ç": "C",
            "ğ": "g", "Ğ": "G",
            "ı": "i", "İ": "I",
            "ö": "o", "Ö": "O",
            "ş": "s", "Ş": "S",
            "ü": "u", "Ü": "U",
        })
        return (text or "").translate(tr_map)

    @staticmethod
    def _tr_lower(text: str) -> str:
        return (text or "").replace("İ", "i").replace("I", "ı").lower()

    def _current_color(self) -> Tuple[int, int, int]:
        return (0, 0, 0) if self.eraser else self.colors[self.color_index]

    def _current_thickness(self) -> int:
        return max(30, self.brush_thickness * 3) if self.eraser else self.brush_thickness

    def _set_brush(self, value: int) -> None:
        self.brush_thickness = int(max(BRUSH_MIN, min(BRUSH_MAX, value)))
        self.assistant_tip = f"Firca kalinligi: {self.brush_thickness}"

    def _get_speaker(self):
        """Türkçe nöral ses motorunu döndürür (Edge TTS → gTTS → yerel ses).
        Dışarıdan bir motor verildiyse o kullanılır; yoksa ilk ihtiyaçta
        yerleşik motor oluşturulur."""
        if self._speaker is None and not self._speaker_failed:
            try:
                from .speech import TurkishSpeaker
                self._speaker = TurkishSpeaker(online=not self.offline)
            except Exception as exc:
                log.warning("[AirCanvas] Türkçe ses motoru başlatılamadı: %s", exc)
                self._speaker_failed = True
        return self._speaker

    def _say_async(self, text: str, wait: bool = False) -> None:
        """Kamera FPS'ini düşürmeden arka planda Türkçe sesli geri bildirim verir.
        Zaten konuşuluyorsa yeni mesaj atlanır (sesler üst üste binmez). ``wait=True`` ise
        atlanmaz; tek bir kuyruk thread'i mesajları sırayla okur — tasarım adımları ve AI cevapları için."""
        if not self.voice_feedback or not text:
            return
        text = clean_for_speech(text)
        if not text:
            return

        if wait:
            self._speech_queue.put((self._speech_gen, text))
            self._ensure_speech_worker()
            return

        speaker = self._get_speaker()
        if speaker is not None:
            try:
                if not speaker.is_busy() and self._speech_queue.empty():
                    speaker.say(text)
                return
            except Exception as exc:
                log.warning("[AirCanvas] Ses motoru hatası: %s", exc)

        threading.Thread(target=self._say_sapi_turkish, args=(text,), daemon=True).start()

    def _ensure_speech_worker(self) -> None:
        if self._speech_worker is None or not self._speech_worker.is_alive():
            self._speech_worker = threading.Thread(target=self._speech_loop, daemon=True,
                                                   name="AirCanvasSpeechQueue")
            self._speech_worker.start()

    def _speech_loop(self) -> None:
        """Kuyruktaki mesajları sırayla okur: her mesaj için önceki konuşmanın bitmesini bekler."""
        while True:
            gen, text = self._speech_queue.get()
            if gen != self._speech_gen:
                continue   # stop_speech() sonrası eskimiş mesaj
            speaker = self._get_speaker()
            if speaker is None:
                self._say_sapi_turkish(text)
                continue
            try:
                deadline = time.time() + 60
                while speaker.is_busy() and time.time() < deadline and gen == self._speech_gen:
                    time.sleep(0.1)
                if gen == self._speech_gen:
                    speaker.say(text)
            except Exception as exc:
                log.warning("[AirCanvas] Ses motoru hatası: %s", exc)

    def _speech_idle(self) -> bool:
        speaker = self._speaker
        busy = False
        if speaker is not None:
            try:
                busy = bool(speaker.is_busy())
            except Exception:
                busy = False
        return self._speech_queue.empty() and not busy

    def stop_speech(self) -> None:
        """Süren konuşmayı keser ve bekleyen mesajları atar (mikrofon asistanın sesini duymasın)."""
        self._speech_gen += 1
        while True:
            try:
                self._speech_queue.get_nowait()
            except queue.Empty:
                break
        stop = getattr(self._speaker, "stop", None)
        if callable(stop):
            try:
                stop()
            except Exception as exc:
                log.debug("[AirCanvas] Konuşma durdurulamadı: %s", exc)

    def _say_sapi_turkish(self, text: str) -> None:
        """Son çare: yalnızca Türkçe bir Windows sesi kuruluysa SAPI ile konuşur.
        Türkçe ses yoksa susar; İngilizce sesle bozuk Türkçe okumaktan iyidir."""
        if not self._speech_lock.acquire(blocking=False):
            return
        com = None   # CoInitialize başarılıysa comtypes modülü; finally'de CoUninitialize için
        try:
            import comtypes  # type: ignore
            import comtypes.client  # type: ignore
            comtypes.CoInitialize()
            com = comtypes
            speaker = comtypes.client.CreateObject("SAPI.SpVoice")
            voices = speaker.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                desc = str(v.GetDescription()).lower()
                if "tolga" in desc or "turkish" in desc or "türkçe" in desc or "tr-tr" in desc:
                    speaker.Voice = v
                    speaker.Speak(text)
                    return
            log.info("[AirCanvas] Türkçe Windows sesi yok; sesli geri bildirim atlandı.")
        except Exception:
            pass
        finally:
            if com is not None:
                try:
                    com.CoUninitialize()
                except Exception:
                    pass
            self._speech_lock.release()

    # ─── Sesli asistan ────────────────────────────────────────────────────────

    def _get_llm(self):
        """Yerel LLM istemcisini önbellekten verir; çevrimdışıysa en fazla dakikada bir yeniden yoklar."""
        now = time.time()
        try:
            if self._llm is None:
                from .llm import LocalLLMClient
                self._llm = LocalLLMClient()
                self._llm_checked_at = now
            elif not getattr(self._llm, "available", False) and now - self._llm_checked_at > 60:
                self._llm_checked_at = now
                self._llm._probe()
        except Exception as exc:
            log.debug("[AirCanvas] Yerel LLM kullanılamıyor: %s", exc)
            return None
        return self._llm if getattr(self._llm, "available", False) else None

    def _get_listener(self):
        """Konuşma tanıyıcıyı (Google / çevrimdışı Whisper) ilk ihtiyaçta kurar."""
        if self._listener is None:
            from .listen import SpeechListener
            self._listener = SpeechListener(engine="whisper" if self.offline else None)
        return self._listener

    def _summarize_speech(self, text: str) -> str:
        """Kullanıcının sesli söylediklerini kısa, net bir özete dönüştürür."""
        cleaned = (text or "").strip()
        if not cleaned:
            return "Ses algılanamadı."

        words = cleaned.split()
        if len(words) <= 10:
            return f"Özet: {cleaned[:1].upper()}{cleaned[1:]}"

        llm = self._get_llm()
        if llm is not None:
            try:
                prompt = (
                    "Kullanıcının çizim yaparken söylediği şu ifadeyi Türkçe olarak tek cümlede "
                    f"(en fazla 12 kelimeyle) özetle:\n\"{cleaned}\""
                )
                resp = llm.generate(
                    prompt,
                    system="Sen Air Canvas çizim asistanısın. Sadece tek cümlelik net Türkçe özet döndür.",
                    temperature=0.3,
                    max_tokens=80,
                )
                if resp and len(resp.strip()) > 3:
                    return f"Özet: {resp.strip().splitlines()[0][:95]}"
            except Exception as exc:
                log.debug("[AirCanvas] LLM özetleme başarısız: %s", exc)

        important = [w for w in words if len(w) > 2][:10]
        return f"Özet: {' '.join(important)}..."

    def _parse_voice_command(self, text: str) -> Tuple[List[Tuple[str, Any]], str]:
        """Sesli ifadeyi tuval eylemlerine çevirir. Tuvale dokunmaz (thread-safe).

        Dönüş: (eylem listesi, asistan mesajı). Eylem yoksa ifade not olarak değerlendirilir.
        """
        low = self._tr_lower(text)
        tokens = re.findall(r"\w+", low)

        def has(*keywords: str) -> bool:
            return any(tok.startswith(kw) for tok in tokens for kw in keywords)

        # ── AI ölçü sorduysa bu konuşma cevaptır ──
        if self._clarify_request and time.time() < self._clarify_until:
            if has("vazgeç", "vazgec", "boşver", "bosver", "iptal"):
                return [("design_cancel", None)], "Tamam, tasarımdan vazgeçtim."
            return [("design_answer", text)], "Teşekkürler, tasarlıyorum."

        # ── 3D tasarım komutları ──
        if self.design is not None:
            if has("taslak", "taslağ", "taslag") or ("tuvale" in tokens and has("çiz", "ciz", "aktar")):
                return [("design_ink", None)], ""
            if has("tasar") and has("kapat", "bitir", "iptal", "sil"):
                return [("design_close", None)], "Tasarım kapatıldı."
            if has("sonraki", "sıradaki", "siradaki", "devam") or "ileri git" in low:
                return [("design_next", None)], ""
            if has("önceki", "onceki") or "geri dön" in low or "geri don" in low:
                return [("design_prev", None)], ""
            if has("tekrar"):
                return [("design_repeat", None)], ""
            if has("genel", "bütün", "butun"):
                return [("design_overview", None)], ""
            if has("rehber", "kılavuz", "kilavuz"):
                return [("guide_toggle", None)], ""
        if has("kontrol", "değerlendir", "degerlendir") or "nasıl olmuş" in low or "nasil olmus" in low:
            return [("design_review", None)], "Çizimine bakıyorum."
        if has("stl", "yazdır", "yazdir") or "dışa aktar" in low or "disa aktar" in low or "baskıya" in low:
            return [("design_export", None)], ""
        from .design import TEMPLATE_KEYWORDS
        if (has("tasar", "modelle") or "3d" in tokens or "üç boyut" in low or "uc boyut" in low
                or has(*TEMPLATE_KEYWORDS)):
            return [("design_request", text)], "Tasarımı hazırlıyorum."

        if "hepsini sil" in low or "tuvali sil" in low or has("temizle"):
            return [("clear", None)], "Tuval temizlendi."
        if "geri al" in low or has("iptal"):
            return [("undo", None)], "Son adım geri alındı."
        if "ileri al" in low or has("yinele"):
            return [("redo", None)], "Adım yinelendi."
        if has("kaydet"):
            return [("save", None)], "Çizim kaydediliyor."
        # "Duvar kalınlığı kaç olmalı?" bir sorudur, "kalın" fırça komutu değil.
        if "?" in text or QUESTION_WORDS.intersection(tokens):
            return [("ask", text)], "Düşünüyorum..."

        actions: List[Tuple[str, Any]] = []
        messages: List[str] = []

        if has("silgi"):
            actions.append(("eraser", None))
            messages.append("Silgi seçildi")
        elif has("kalem"):
            actions.append(("pen", None))
            messages.append("Kalem seçildi")

        if has("kalın", "kalin", "büyüt", "buyut"):
            actions.append(("brush_up", None))
            messages.append("Fırça kalınlaştırıldı")
        elif has("ince", "küçült", "kucult"):
            actions.append(("brush_down", None))
            messages.append("Fırça inceltildi")

        if has("tahta"):
            actions.append(("board_set", True))
            messages.append("Tahta moduna geçildi")
        elif has("kamera"):
            actions.append(("board_set", False))
            messages.append("Kamera moduna geçildi")

        # "kırmızı daire ve mavi kare çiz": her parça kendi rengi, şekli, boyutu ve konumuyla.
        stamps: List[Tuple[int, Dict[str, Any]]] = []
        stamp_colors: List[str] = []
        for segment in re.split(r"\s*(?:[,;]|\bve\b|\bile\b|\bsonra\b|\bardından\b|\bardindan\b)\s*", low):
            seg_tokens = re.findall(r"\w+", segment)
            if not seg_tokens:
                continue
            color, shape = self._voice_color(seg_tokens), self._voice_shape(seg_tokens)
            color_word = _spoken(PALETTE[color][0]) if color is not None else ""
            if color is not None:
                actions.append(("color", color))
                if not shape:
                    messages.append(f"Kalem rengi {color_word} yapıldı")
            if shape:
                spec = self._voice_shape_spec(seg_tokens, shape)
                actions.append(("stamp", spec))
                stamps.append((len(actions) - 1, spec))
                stamp_colors.append(color_word)
        self._layout_stamps(actions, stamps)
        if stamps and all(spec.get("auto") for _, spec in stamps):
            names = [self._stamp_name(spec, color) for (_, spec), color in zip(stamps, stamp_colors)]
            together = " ve ".join(names)
            messages.append(f"{together[:1].upper()}{together[1:]} yan yana çizildi")
        else:
            for (_, spec), color in zip(stamps, stamp_colors):
                messages.append(self._stamp_message(spec, color))

        if not actions:
            # Tasarım açıkken veya soru sorulduğunda konuşma AI'a soru olarak gider.
            if self.design is not None or "?" in text or QUESTION_WORDS.intersection(tokens):
                return [("ask", text)], "Düşünüyorum..."
            return [], "Sesli notunuz kaydedildi."
        return actions, ". ".join(messages) + "."

    @staticmethod
    def _voice_color(tokens: List[str]) -> Optional[int]:
        for keywords, index in VOICE_COLORS:
            if any(t.startswith(k) for t in tokens for k in keywords):
                return index
        return None

    @staticmethod
    def _voice_shape(tokens: List[str]) -> str:
        for keywords, shape in VOICE_SHAPES:
            if any(t.startswith(k) for t in tokens for k in keywords):
                if shape == "DUZ CIZGI":
                    # "çizgiyi kalınlaştır" bir fırça komutudur; çizgi için açık bir çizme fiili gerekir.
                    verb = any((t.startswith(("çiz", "ciz")) and not t.startswith(("çizgi", "cizgi")))
                               or t.startswith(("ekle", "koy")) for t in tokens)
                    if not verb:
                        continue
                return shape
        return ""

    @staticmethod
    def _voice_shape_spec(tokens: List[str], shape: str) -> Dict[str, Any]:
        spec: Dict[str, Any] = {"shape": shape}
        for keywords, scale, _ in VOICE_SIZES:
            if any(t.startswith(k) for t in tokens for k in keywords):
                spec["scale"] = scale
                break
        ax = -1.0 if VOICE_PLACES["left"] & set(tokens) else 1.0 if VOICE_PLACES["right"] & set(tokens) else 0.0
        ay = -1.0 if VOICE_PLACES["up"] & set(tokens) else 1.0 if VOICE_PLACES["down"] & set(tokens) else 0.0
        if ax or ay or VOICE_PLACES["center"] & set(tokens):
            spec["anchor"] = (ax, ay)
        return spec

    @staticmethod
    def _layout_stamps(actions: List[Tuple[str, Any]], stamps: List[Tuple[int, Dict[str, Any]]]) -> None:
        """Konumu söylenmemiş birden çok şekli yan yana dizer; tek ve sade şekil adıyla kalır."""
        if len(stamps) > 1:
            free = [spec for _, spec in stamps if "anchor" not in spec]
            for i, spec in enumerate(free):
                spec["anchor"] = (-1.0 + 2.0 * (i + 0.5) / len(free), 0.0) if len(free) > 1 else (0.0, 0.0)
                spec.setdefault("scale", 0.75 if len(stamps) == 2 else 0.6)
                spec["auto"] = True
        for index, spec in stamps:
            if set(spec) == {"shape"}:
                actions[index] = ("stamp", spec["shape"])   # Geriye uyumlu: ("stamp", "DAIRE")

    @staticmethod
    def _stamp_name(spec: Dict[str, Any], color_word: str = "") -> str:
        size = next((word for _, scale, word in VOICE_SIZES if spec.get("scale") == scale), "")
        return " ".join(w for w in (size, color_word, _spoken(spec["shape"])) if w)

    @classmethod
    def _stamp_message(cls, spec: Dict[str, Any], color_word: str = "") -> str:
        ax, ay = spec.get("anchor", (0.0, 0.0))
        horiz = {-1.0: "Sol", 1.0: "Sağ"}.get(ax, "")
        vert = {-1.0: "üst", 1.0: "alt"}.get(ay, "")
        if horiz and vert:
            place = f"{horiz} {vert}{'e' if vert == 'üst' else 'a'}"
        elif horiz:
            place = horiz + "a"
        elif vert:
            place = {"üst": "Üste", "alt": "Alta"}[vert]
        else:
            place = "Merkeze"
        return f"{place} {cls._stamp_name(spec, color_word)} çizildi"

    def _listen_and_summarize_async(self) -> None:
        """Mikrofonu dinler; komutsa eylem kuyruğuna ekler, değilse not olarak özetler."""
        if self.voice_listening:
            return
        self.voice_listening = True
        self.assistant_tip = "Asistan dinliyor... Konusun (orn. 'dron tasarla', 'sonraki', 'kontrol et')"

        # Asistan konuşuyorsa sustur: mikrofon kendi sesini komut sanmasın.
        self.stop_speech()

        def _worker():
            try:
                time.sleep(0.15)   # Hoparlördeki son sesin sönmesi için kısa bekleme
                from .listen import ListenError
                try:
                    spoken_text = (self._get_listener().listen() or "").strip()
                    if not spoken_text:
                        raise ListenError("Söylediğinizi anlayamadım, lütfen tekrar söyleyin.", "unclear")
                except ListenError as exc:
                    log.info("[AirCanvas] Dinleme: %s", exc.message)
                    self.assistant_tip = exc.message + " (Tekrar: V)"
                    self._say_async(exc.message)
                    return
                except Exception as exc:
                    log.warning("[AirCanvas] Dinleme hatası: %s", exc)
                    self.assistant_tip = "Dinleme sirasinda hata oldu. Tekrar denemek icin 'V' tusuna basin."
                    self._say_async("Dinlerken bir hata oldu, lütfen tekrar deneyin.")
                    return

                self.voice_raw = spoken_text
                actions, message = self._parse_voice_command(spoken_text)
                if actions:
                    for action in actions:
                        self._actions.put(action)
                    if message:
                        self.assistant_tip = message
                        if actions[0][0] not in ("ask", "design_request", "design_review", "design_answer"):
                            self._say_async(message)   # Uzun işlerde sonuç ayrıca seslendirilir
                    return

                summary = self._summarize_speech(spoken_text)
                self.voice_summary = summary
                self.voice_notes.append(f"[{time.strftime('%H:%M:%S')}] {spoken_text}\n    {summary}")
                self.assistant_tip = message
                self._say_async(summary.replace("Özet:", "Sesli notunuz:"))
            finally:
                self.voice_listening = False

        threading.Thread(target=_worker, daemon=True).start()

    # ─── Eylemler ─────────────────────────────────────────────────────────────

    def _drain_actions(self) -> None:
        """Diğer thread'lerden gelen eylemleri döngü thread'inde uygular."""
        while True:
            try:
                action, value = self._actions.get_nowait()
            except queue.Empty:
                return
            self._perform(action, value)

    def _perform(self, action: str, value: Any = None) -> None:
        if action in ("clear", "undo", "redo", "stamp", "save") and self._stroke_owner:
            self._end_stroke()

        if action == "color":
            self.color_index = int(value)
            self.eraser = False
        elif action == "eraser":
            self.eraser = True
            self.assistant_tip = "Silgi secildi."
        elif action == "pen":
            self.eraser = False
        elif action == "brush_up":
            self._set_brush(self.brush_thickness + BRUSH_STEP)
        elif action == "brush_down":
            self._set_brush(self.brush_thickness - BRUSH_STEP)
        elif action == "shape_toggle":
            self.shape_assist = not self.shape_assist
            self.assistant_tip = f"Sekil duzeltme {'acik' if self.shape_assist else 'kapali'}."
        elif action == "board_toggle":
            self.board_mode = not self.board_mode
        elif action == "board_set":
            self.board_mode = bool(value)
        elif action == "hud_toggle":
            self.show_hud = not self.show_hud
        elif action == "clear":
            if self.canvas.any():
                self._push_undo()
                self.canvas[:] = 0
            self.assistant_tip = "Tuval temizlendi."
        elif action == "undo":
            self._undo()
        elif action == "redo":
            self._redo()
        elif action == "stamp":
            self._stamp_shape(value)
        elif action == "save":
            self._save()
        elif action == "voice":
            self._listen_and_summarize_async()
        elif action == "fullscreen_toggle":
            self.fullscreen = not self.fullscreen
            self._window_dirty = True
        # ── 3D tasarım ──
        elif action == "design_request":
            self._start_design(str(value))
        elif action == "design_loaded":
            self._on_design_loaded(*value)
        elif action == "design_next":
            self._design_goto(self.design_step + 1)
        elif action == "design_prev":
            self._design_goto(self.design_step - 1)
        elif action == "design_repeat":
            self._design_goto(self.design_step)
        elif action == "design_overview":
            self._design_goto(-1)
        elif action == "guide_toggle":
            self.show_guide = not self.show_guide
            self.assistant_tip = f"3D rehber {'acik' if self.show_guide else 'gizli'}."
        elif action == "design_export":
            self._export_design()
        elif action == "design_review":
            self._review_drawing()
        elif action == "design_clarify":
            self._on_design_clarify(*value)
        elif action == "design_answer":
            self._on_design_answer(str(value))
        elif action == "design_cancel":
            self._clarify_request = ""
            self.assistant_tip = "Tasarimdan vazgecildi."
        elif action == "design_ink":
            self._ink_design()
        elif action == "design_close":
            self.design = None
            self._guide = None
            self.assistant_tip = "Tasarim kapatildi."
        elif action == "ask":
            self._ask_async(str(value))

    def _press_button(self, button_id: str, from_gesture: bool = False) -> None:
        """Üst menü butonunu uygular; el hareketiyle basılan butonlarda bekleme süresi uygulanır."""
        if from_gesture and button_id in BUTTON_COOLDOWNS:
            now = time.time()
            if now - self._cooldowns.get(button_id, 0.0) < BUTTON_COOLDOWNS[button_id]:
                return
            self._cooldowns[button_id] = now

        if button_id.startswith("color_"):
            self._perform("color", int(button_id.split("_", 1)[1]))
        else:
            mapping = {
                "eraser": "eraser", "shape": "shape_toggle", "board": "board_toggle",
                "clear": "clear", "undo": "undo", "save": "save", "voice": "voice",
            }
            if button_id in mapping:
                self._perform(mapping[button_id])

    def _handle_key(self, key: int) -> None:
        char = chr(key).lower() if 0 <= key < 256 else ""
        key_actions = {
            "v": "voice", "z": "undo", "y": "redo", "c": "clear", "s": "save",
            "b": "board_toggle", "h": "hud_toggle", "a": "shape_toggle",
            "+": "brush_up", "=": "brush_up", "-": "brush_down",
        }
        key_actions.update({
            "f": "fullscreen_toggle", "n": "design_next", "p": "design_prev", "r": "design_repeat",
            "g": "guide_toggle", "x": "design_export", "k": "design_review", "t": "design_ink",
        })
        if char in key_actions:
            self._perform(key_actions[char])
        elif char == "e":
            self._perform("pen" if self.eraser else "eraser")
        elif char.isdigit() and 1 <= int(char) <= len(PALETTE):
            self._perform("color", int(char) - 1)

    # ─── 3D tasarım asistanı ──────────────────────────────────────────────────

    def _get_assistant(self):
        if self._assistant is None:
            from .ai import DesignAssistant
            self._assistant = DesignAssistant(local_llm=self._get_llm, mode=self.ai_mode)
            self._assistant.status_callback = self._set_design_status
        return self._assistant

    def _set_design_status(self, text: str) -> None:
        if self.design_busy:
            self.assistant_tip = text

    def _start_design(self, request: str, clarified: bool = False) -> None:
        """İsteği arka planda tasarıma çevirir; sonuç eylem kuyruğuyla döngüye döner.

        AI kritik bir ölçünün eksik olduğuna karar verirse önce tek bir soru sorar
        (``design_clarify``); cevap gelince istek bu bilgiyle yeniden başlatılır."""
        if self.design_busy:
            self.assistant_tip = "Bir tasarim zaten hazirlaniyor, lutfen bekleyin."
            return
        self.design_busy = True
        self.assistant_tip = f"AI tasarliyor: {request[:70]} ..."
        self._say_async("Tamam, tasarlıyorum. Bu biraz sürebilir.")

        def _worker():
            design, source = None, ""
            try:
                assistant = self._get_assistant()
                clarify = getattr(assistant, "clarify", None)
                if not clarified and callable(clarify):
                    question = clarify(request)
                    if question:
                        self._actions.put(("design_clarify", (request, question)))
                        return
                design, source = assistant.design(request)
            except Exception as exc:
                log.error("[AirCanvas] Tasarım üretilemedi: %s", exc)
            finally:
                self.design_busy = False
            self._actions.put(("design_loaded", (design, source, request)))

        threading.Thread(target=_worker, daemon=True, name="AirCanvasDesign").start()

    def _on_design_clarify(self, request: str, question: str) -> None:
        """AI'ın ölçü sorusunu sorar ve konuşma bitince cevabı dinler."""
        self._clarify_request = request
        self._clarify_until = time.time() + 120
        self._show_answer(question)
        self.assistant_tip = f"{question} (Cevap icin V; 'sen karar ver' de diyebilirsin)"
        self._say_async(question, wait=True)
        if self.voice_feedback:
            def _listen_when_quiet():
                time.sleep(0.4)
                deadline = time.time() + 45
                while not self._speech_idle() and time.time() < deadline:
                    time.sleep(0.1)
                if self._clarify_request:
                    self._actions.put(("voice", None))
            threading.Thread(target=_listen_when_quiet, daemon=True, name="AirCanvasClarify").start()

    def _on_design_answer(self, answer: str) -> None:
        request, self._clarify_request = self._clarify_request, ""
        if not request:
            return
        low = self._tr_lower(answer)
        if any(k in low for k in ("sen karar", "sen seç", "sen sec", "fark etmez", "farketmez", "bilmiyorum",
                                  "varsayılan", "varsayilan", "standart", "sana bırak", "sana birak")):
            answer = "Ölçüleri sen seç; yaygın standart değerleri kullan."
        self._start_design(f"{request}. Kullanıcının ek bilgisi: {answer}", clarified=True)

    def _ink_design(self) -> None:
        """3D rehberin taslağını (dış hatlar ve keskin kenarlar) tuvale mürekkep olarak çizer."""
        if self.design is None:
            self.assistant_tip = "Taslak icin once bir tasarim isteyin."
            self._say_async("Önce bir tasarım isteyin, örneğin dron tasarla.")
            return
        from .design import render_preview
        x1, y1, x2, y2 = GUIDE_RECT
        lines = self._outline(render_preview(self.design, x2 - x1, y2 - y1), self.brush_thickness)
        if not lines.any():
            return
        if self._stroke_owner:
            self._end_stroke(allow_snap=False)
        self._push_undo()
        self.canvas[y1:y2, x1:x2][lines] = self.colors[self.color_index]
        self.assistant_tip = "Taslak tuvale cizildi. (Geri al: Z)"
        self._say_async("Taslağı tuvale çizdim. Üzerinden geçebilir veya kaydedebilirsin.")

    @staticmethod
    def _outline(img: np.ndarray, brush: int) -> np.ndarray:
        """Gölgeli önizlemeden çizgi maskesi: siluet, delik kenarları ve yüzeyler arası keskin kenarlar."""
        mask = img.any(axis=2).astype(np.uint8) * 255
        edges = cv2.Canny(cv2.cvtColor(img, cv2.COLOR_BGR2GRAY), 40, 110)
        contours, _ = cv2.findContours(mask, cv2.RETR_LIST, cv2.CHAIN_APPROX_NONE)
        cv2.drawContours(edges, contours, -1, 255, 1)
        k = max(1, min(4, brush // 6))
        if k > 1:
            edges = cv2.dilate(edges, cv2.getStructuringElement(cv2.MORPH_ELLIPSE, (k, k)))
        return edges > 0

    def _on_design_loaded(self, design, source: str, request: str) -> None:
        if design is None:
            from .design import TEMPLATE_HINT
            if self.ai_mode == "local":
                msg = ("Yerel model bunu tasarlayamadı. LM Studio veya Ollama'da bir model yüklü mü? "
                       f"İnternetsiz şunları da yapabilirim: {TEMPLATE_HINT}.")
            else:
                msg = ("Bunu tasarlayamadım. Claude bağlı değilken şunları yapabilirim: "
                       f"{TEMPLATE_HINT}.")
            self.assistant_tip = msg
            self._say_async(msg, wait=True)
            return
        self.design = design
        self.design_step = 0
        self.show_guide = True
        self._refresh_guide()
        failed = f" Üretilemeyen parça: {', '.join(design.failed_parts)}." if design.failed_parts else ""
        self.assistant_tip = f"Tasarim hazir ({source}): {design.name}. 'Sonraki' ile ilerleyin, 'STL' ile kaydedin."
        self._say_async(f"{design.name} hazır. {design.summary}{failed} {design.spoken_step(0)}", wait=True)

    def _refresh_guide(self) -> None:
        if self.design is None:
            self._guide = None
            return
        from .design import render_preview
        x1, y1, x2, y2 = GUIDE_RECT
        highlight = self.design_step if self.design_step >= 0 else None
        img = render_preview(self.design, x2 - x1, y2 - y1, highlight=highlight)
        self._guide = (img, np.any(img, axis=2))

    def _design_goto(self, step: int) -> None:
        if self.design is None:
            self.assistant_tip = "Acik bir tasarim yok. Ornek: 'dron tasarla' veya 'M3 vida ciz'."
            self._say_async("Önce bir tasarım isteyin, örneğin dron tasarla.")
            return
        last = len(self.design.parts) - 1
        if step > last:
            msg = "Tüm parçaları bitirdik. STL deyince dosyaları baskıya hazır kaydederim."
            if self.design.print_tips:
                msg += " İpucu: " + self.design.print_tips[0]
            self.assistant_tip = msg
            self._say_async(msg, wait=True)
            return
        self.design_step = max(-1, step)
        self._refresh_guide()
        if self.design_step < 0:
            self.assistant_tip = f"Genel gorunum: {self.design.name}"
            self._say_async(self.design.overview_text(), wait=True)
        else:
            self.assistant_tip = self.design.step_text(self.design_step).splitlines()[0]
            self._say_async(self.design.spoken_step(self.design_step), wait=True)

    def _export_design(self) -> str:
        if self.design is None:
            self.assistant_tip = "Disa aktarilacak tasarim yok. Once bir sey tasarlatin."
            self._say_async("Önce bir tasarım isteyin.")
            return ""
        try:
            from .design import export_design
            folder = export_design(self.design, os.path.join(self.save_dir, "3D"))
        except Exception as exc:
            log.error("[AirCanvas] STL dışa aktarılamadı: %s", exc)
            self.assistant_tip = "STL kaydedilemedi."
            self._say_async("STL dosyalarını kaydedemedim.")
            return ""
        self.last_export_dir = folder
        count = sum(1 for _, t in self.design.meshes if len(t))
        self.assistant_tip = f"STL kaydedildi ({count} parca): {folder}"
        self._say_async(f"{count} parça STL olarak kaydedildi. Talimatlar da klasörde.", wait=True)
        return folder

    def _ai_busy_notice(self) -> None:
        self.assistant_tip = "Hala onceki istegi dusunuyorum, biraz bekleyin."
        self._say_async("Hâlâ önceki isteğin üzerinde düşünüyorum, biraz bekle.")

    def _ask_async(self, question: str) -> None:
        if self.ai_busy:
            self._ai_busy_notice()
            return
        self.ai_busy = True
        design, step = self.design, self.design_step

        def _worker():
            try:
                answer = self._get_assistant().ask(question, design, step)
            except Exception as exc:
                log.error("[AirCanvas] AI sorusu başarısız: %s", exc)
                answer = ""
            finally:
                self.ai_busy = False
            if not answer:
                answer = "Şu an yapay zekâya ulaşamıyorum. Claude anahtarı veya yerel model gerekiyor."
            self.voice_summary = f"Soru: {question}"
            self._show_answer(answer)
            self._say_async(answer, wait=True)

        threading.Thread(target=_worker, daemon=True, name="AirCanvasAsk").start()

    def _show_answer(self, text: str) -> None:
        """Uzun AI cevabını ekranda okunacak kadar süre gösterir (HUD satırı kısa kalır)."""
        clean = clean_markdown(text)
        self.assistant_tip = " ".join(clean.splitlines())
        self._answer_text = clean
        self._answer_until = time.time() + max(8.0, min(40.0, len(clean) / 12.0))

    def _review_drawing(self) -> None:
        """Ekran görüntüsünü (çizim + rehber) AI'a gösterip geri bildirim ister."""
        if self.ai_busy:
            self._ai_busy_notice()
            return
        if self._last_view is None:
            self.assistant_tip = "Henuz degerlendirilecek bir goruntu yok."
            return
        view = self._last_view
        scale = 1024 / view.shape[1]
        small = cv2.resize(view, (1024, int(view.shape[0] * scale))) if scale < 1 else view
        ok, buf = cv2.imencode(".png", small)
        if not ok:
            return
        self.ai_busy = True
        self.assistant_tip = "Cizimine bakiyorum..."
        design, step, png = self.design, self.design_step, buf.tobytes()

        def _worker():
            try:
                answer = self._get_assistant().review(png, design, step)
            except Exception as exc:
                log.error("[AirCanvas] Çizim değerlendirilemedi: %s", exc)
                answer = ""
            finally:
                self.ai_busy = False
            if not answer:
                answer = "Çizimi değerlendirmek için Claude ya da görsel destekli bir yerel model gerekiyor."
            self._show_answer(answer)
            self._say_async(answer, wait=True)

        threading.Thread(target=_worker, daemon=True, name="AirCanvasReview").start()

    # ─── Geçmiş (geri al / yinele) ────────────────────────────────────────────

    def _push_undo(self, snapshot: Optional[np.ndarray] = None) -> None:
        self.undo_stack.append(self.canvas.copy() if snapshot is None else snapshot)
        self.redo_stack.clear()

    def _undo(self) -> bool:
        if not self.undo_stack:
            self.assistant_tip = "Geri alinacak adim yok."
            return False
        self.redo_stack.append(self.canvas.copy())
        self.canvas[:] = self.undo_stack.pop()
        self.assistant_tip = "Son adim geri alindi. (Yinele: Y)"
        return True

    def _redo(self) -> bool:
        if not self.redo_stack:
            self.assistant_tip = "Yinelenecek adim yok."
            return False
        self.undo_stack.append(self.canvas.copy())
        self.canvas[:] = self.redo_stack.pop()
        self.assistant_tip = "Adim yinelendi."
        return True

    # ─── Çizgi yönetimi ───────────────────────────────────────────────────────

    def _begin_stroke(self, pt: Tuple[int, int], owner: str) -> None:
        self._stroke_owner = owner
        self._stroke_base = self.canvas.copy()
        self._stroke_points = []
        self._stroke_dirty = False
        self._last_point = None
        self._extend_stroke(pt)

    def _extend_stroke(self, pt: Tuple[int, int]) -> None:
        if self._stroke_owner is None:
            return
        pt = (int(pt[0]), int(pt[1]))
        if pt[1] < DRAW_MIN_Y:
            # Menü alanına girildi: çizgiyi kes, mürekkep bırakma
            self._last_point = None
            return
        if self._last_point is not None:
            cv2.line(self.canvas, self._last_point, pt, self._current_color(), self._current_thickness())
            self._stroke_dirty = True
        self._stroke_points.append(pt)
        self._last_point = pt

    def _end_stroke(self, allow_snap: bool = True) -> str:
        """Aktif çizgiyi bitirir: geri alma adımı ekler ve gerekirse şekle dönüştürür."""
        if self._stroke_owner is None:
            return ""
        base, points, dirty = self._stroke_base, self._stroke_points, self._stroke_dirty
        owner = self._stroke_owner
        self._stroke_owner = None
        self._stroke_base = None
        self._stroke_points = []
        self._stroke_dirty = False
        self._last_point = None

        if base is None:
            return ""
        if not dirty:
            # Fareyle tek tıklama nokta bırakır (el için atlanır: titreme kaynaklı lekeleri önler)
            if owner == "mouse" and points:
                cv2.circle(self.canvas, points[0], max(1, self._current_thickness() // 2), self._current_color(), cv2.FILLED)
                self._push_undo(base)
            return ""
        self._push_undo(base)

        if not (allow_snap and self.shape_assist and not self.eraser):
            return ""
        shape = self._classify_stroke(points)
        if not shape:
            return ""

        # Çizgi öncesi tuvale dön ve yalnızca düzgün şekli çiz (diğer çizimler korunur)
        self.canvas[:] = base
        self._draw_shape(self.canvas, shape, self._current_color(), self.brush_thickness)
        name = shape["name"]
        self._last_shape_label = f"SEKIL ALGILANDI: {name}"
        self._last_shape_time = time.time()
        self.assistant_tip = f"{name} algilandi ve duzeltildi. (Geri al: Z)"
        spoken = _spoken(name)
        self._say_async(f"{spoken[:1].upper()}{spoken[1:]} algılandı.")
        return name

    # ─── Şekil tanıma ─────────────────────────────────────────────────────────

    @staticmethod
    def _classify_stroke(points: List[Tuple[int, int]]) -> Optional[Dict[str, Any]]:
        """Parmak izini analiz eder; düz çizgi, üçgen, kare/dikdörtgen, daire veya elips döndürür."""
        if len(points) < MIN_STROKE_POINTS:
            return None
        pts = np.array(points, dtype=np.int32).reshape(-1, 1, 2)
        peri = cv2.arcLength(pts, False)
        if peri < 110:
            return None

        start = pts[0, 0].astype(float)
        end = pts[-1, 0].astype(float)
        close_dist = float(np.linalg.norm(start - end))

        # 1. Düz çizgi: uç noktalar arası mesafe toplam yola çok yakın
        if close_dist >= peri * 0.88 and close_dist > 110:
            return {"name": "DUZ CIZGI", "kind": "line",
                    "p1": (int(start[0]), int(start[1])), "p2": (int(end[0]), int(end[1]))}

        # 2. Kapalı şekil: başlangıç ve bitiş, hem çizgi uzunluğuna hem şeklin boyutuna göre yakın olmalı
        _, _, bw, bh = cv2.boundingRect(pts)
        if close_dist > peri * 0.36 or close_dist > 0.35 * max(bw, bh):
            return None

        hull = cv2.convexHull(pts)
        area = cv2.contourArea(hull)
        if area < 1600:
            return None
        hull_peri = cv2.arcLength(hull, True)
        if hull_peri <= 0:
            return None

        approx = cv2.approxPolyDP(hull, 0.04 * hull_peri, True)
        circularity = 4 * np.pi * area / (hull_peri * hull_peri)
        (cx, cy), (rw, rh), angle = cv2.minAreaRect(hull)
        # Dolgunluk: dikdörtgen ≈ 1.0, daire/elips ≈ 0.785, üçgen ≈ 0.5
        extent = area / max(rw * rh, 1.0)

        if len(approx) == 3:
            return {"name": "UCGEN", "kind": "poly", "points": approx.reshape(-1, 2).astype(np.int32)}

        if len(approx) == 4 and extent >= 0.88:
            # Neredeyse yatay/dikey dikdörtgenleri tam hizala
            rem = angle % 90
            if rem < 8:
                angle = 0.0
            elif rem > 82:
                angle, rw, rh = 0.0, rh, rw
            ratio = min(rw, rh) / max(rw, rh, 1)
            name = "KARE" if ratio >= 0.82 else "DIKDORTGEN"
            if name == "KARE":
                rw = rh = (rw + rh) / 2
            box = cv2.boxPoints(((cx, cy), (rw, rh), angle))
            return {"name": name, "kind": "poly", "points": np.round(box).astype(np.int32)}

        if 0.68 <= extent < 0.9 and circularity >= 0.6:
            flat = pts.reshape(-1, 2).astype(np.float32)
            if len(flat) >= 5:
                (ex, ey), (ax1, ax2), ang = cv2.fitEllipse(flat)
                if max(ax1, ax2) > 0 and min(ax1, ax2) / max(ax1, ax2) < 0.75:
                    return {"name": "ELIPS", "kind": "ellipse",
                            "box": ((float(ex), float(ey)), (float(ax1), float(ax2)), float(ang))}
            m = cv2.moments(hull)
            if m["m00"] > 0:
                cx, cy = m["m10"] / m["m00"], m["m01"] / m["m00"]
            else:
                cx, cy = flat.mean(axis=0)
            radius = float(np.mean(np.linalg.norm(flat - np.array([cx, cy]), axis=1)))
            return {"name": "DAIRE", "kind": "circle", "center": (int(round(cx)), int(round(cy))),
                    "radius": int(round(radius))}

        return None

    @staticmethod
    def _draw_shape(canvas: np.ndarray, shape: Dict[str, Any], color, thickness: int) -> None:
        kind = shape["kind"]
        if kind == "line":
            cv2.line(canvas, shape["p1"], shape["p2"], color, thickness)
        elif kind == "poly":
            cv2.polylines(canvas, [shape["points"].reshape(-1, 1, 2)], True, color, thickness)
        elif kind == "circle":
            cv2.circle(canvas, shape["center"], shape["radius"], color, thickness)
        elif kind == "ellipse":
            cv2.ellipse(canvas, shape["box"], color, thickness)

    @staticmethod
    def _stamp_geometry(name: str, cx: float, cy: float, r: float) -> Optional[Dict[str, Any]]:
        """Sesli komut şekli: merkez (cx, cy) ve temel yarıçap r ile çizim tanımı."""
        def poly(points):
            return {"name": name, "kind": "poly", "points": np.round(np.array(points)).astype(np.int32)}

        if name == "DAIRE":
            return {"name": name, "kind": "circle", "center": (int(cx), int(cy)), "radius": int(r)}
        if name == "KARE":
            return poly([[cx - r, cy - r], [cx + r, cy - r], [cx + r, cy + r], [cx - r, cy + r]])
        if name == "DIKDORTGEN":
            w, h = 1.4 * r, 0.8 * r
            return poly([[cx - w, cy - h], [cx + w, cy - h], [cx + w, cy + h], [cx - w, cy + h]])
        if name == "UCGEN":
            return poly([[cx, cy - r], [cx - 1.15 * r, cy + 0.92 * r], [cx + 1.15 * r, cy + 0.92 * r]])
        if name == "ELIPS":
            return {"name": name, "kind": "ellipse", "box": ((float(cx), float(cy)), (2.8 * r, 1.6 * r), 0.0)}
        if name == "ALTIGEN":
            return poly([[cx + r * np.cos(np.radians(a)), cy + r * np.sin(np.radians(a))] for a in range(0, 360, 60)])
        if name == "YILDIZ":
            return poly([[cx + (r if k % 2 == 0 else 0.42 * r) * np.cos(np.radians(-90 + 36 * k)),
                          cy + (r if k % 2 == 0 else 0.42 * r) * np.sin(np.radians(-90 + 36 * k))] for k in range(10)])
        if name == "DUZ CIZGI":
            return {"name": name, "kind": "line", "p1": (int(cx - 1.4 * r), int(cy)), "p2": (int(cx + 1.4 * r), int(cy))}
        return None

    def _stamp_shape(self, value: Any) -> None:
        """Sesli komutla hazır şekil çizer. ``value``: şekil adı ("DAIRE") veya
        {"shape", "scale" (1.0), "anchor" ((-1..1, -1..1); 0,0 = çizim alanının ortası)}."""
        spec = value if isinstance(value, dict) else {"shape": str(value)}
        name = str(spec.get("shape", ""))
        r = STAMP_RADIUS * float(spec.get("scale", 1.0))
        ax, ay = spec.get("anchor", (0.0, 0.0))
        half_w = 1.45 * r if name in ("DIKDORTGEN", "ELIPS", "DUZ CIZGI") else 1.2 * r
        mid_x, mid_y = FRAME_W / 2, (DRAW_MIN_Y + HUD_TOP) / 2
        room_x = max(0.0, FRAME_W / 2 - 30 - half_w)
        room_y = max(0.0, (HUD_TOP - DRAW_MIN_Y) / 2 - 20 - r)
        shape = self._stamp_geometry(name, mid_x + ax * room_x, mid_y + ay * room_y, r)
        if shape is None:
            return
        self._push_undo()
        self._draw_shape(self.canvas, shape, self.colors[self.color_index], self.brush_thickness)

    # ─── Kaydetme ─────────────────────────────────────────────────────────────

    @staticmethod
    def _write_image(path: str, image: np.ndarray) -> bool:
        """Türkçe/Unicode yollarda da çalışan PNG yazımı."""
        ok, buf = cv2.imencode(".png", image)
        if not ok:
            return False
        buf.tofile(path)
        return True

    def _save(self) -> str:
        """Çizimi şeffaf PNG, ekran görüntüsü ve (varsa) sesli notlarla birlikte kaydeder."""
        stamp = time.strftime("%Y%m%d_%H%M%S")
        try:
            os.makedirs(self.save_dir, exist_ok=True)
        except OSError as exc:
            log.error("[AirCanvas] Kayıt klasörü oluşturulamadı: %s", exc)
            self.assistant_tip = "Kaydetme basarisiz oldu."
            return ""
        base = os.path.join(self.save_dir, f"air_canvas_{stamp}")
        suffix = 1
        while os.path.exists(base + ".png"):
            base = os.path.join(self.save_dir, f"air_canvas_{stamp}_{suffix}")
            suffix += 1

        try:
            mask = self.canvas.any(axis=2)
            bgra = np.zeros((FRAME_H, FRAME_W, 4), np.uint8)
            bgra[..., :3] = self.canvas
            bgra[..., 3] = np.where(mask, 255, 0).astype(np.uint8)
            if not self._write_image(base + ".png", bgra):
                raise OSError("PNG kodlanamadı")
            if self._last_view is not None:
                self._write_image(base + "_ekran.png", self._last_view)
            if self.voice_notes:
                with open(base + "_notlar.txt", "w", encoding="utf-8") as fh:
                    fh.write("Air Canvas sesli notları\n\n" + "\n".join(self.voice_notes) + "\n")
        except Exception as exc:
            log.error("[AirCanvas] Kaydetme başarısız: %s", exc)
            self.assistant_tip = "Kaydetme basarisiz oldu."
            return ""

        self.last_saved_path = base + ".png"
        self._session_saves += 1
        notes = " + sesli notlar" if self.voice_notes else ""
        self.assistant_tip = f"Kaydedildi: {os.path.basename(base)}.png{notes}"
        return self.last_saved_path

    # ─── El algılama ──────────────────────────────────────────────────────────

    def _try_init(self) -> bool:
        """El algılama motorunu (Solutions veya Tasks API) başlatır."""
        global mp, MP_OK, MP_TASKS_OK, mp_python, mp_vision
        if mp is None:
            try:
                import mediapipe as _mp
                mp = _mp
                MP_OK = hasattr(mp, 'solutions')
            except Exception:
                mp = None
                MP_OK = False

        if mp is not None and not MP_TASKS_OK:
            try:
                from mediapipe.tasks import python as _mp_python
                from mediapipe.tasks.python import vision as _mp_vision
                mp_python = _mp_python
                mp_vision = _mp_vision
                MP_TASKS_OK = True
            except Exception:
                MP_TASKS_OK = False

        # Eski Solutions API'si mediapipe 0.10.x ile sınırlı; yeni sürümlerde yoktur.
        solutions = getattr(mp, "solutions", None) if mp is not None else None
        if MP_OK and solutions is not None:
            try:
                self.mp_hands = solutions.hands
                self.hands = self.mp_hands.Hands(
                    max_num_hands=1,
                    min_detection_confidence=0.75,
                    min_tracking_confidence=0.75,
                )
                self.available = True
                return True
            except Exception as exc:
                log.warning("[AirCanvas] mp.solutions.hands başlatılamadı, Tasks API denenecek: %s", exc)

        if MP_TASKS_OK:
            self.available = self._init_tasks_detector()
            return self.available

        self.available = False
        return False

    def _ensure_model_exists(self, model_path: str) -> bool:
        """El algılama modeli yoksa otomatik indirir."""
        os.makedirs(os.path.dirname(model_path), exist_ok=True)
        if os.path.exists(model_path) and os.path.getsize(model_path) > 1024:
            return True
        try:
            log.info("[AirCanvas] MediaPipe HandLandmarker modeli indiriliyor...")
            urllib.request.urlretrieve(HAND_MODEL_URL, model_path)
            log.info("[AirCanvas] Model başarıyla indirildi: %s", model_path)
            return True
        except Exception as exc:
            log.error("[AirCanvas] El modeli indirilemedi: %s", exc)
            return False

    def _init_tasks_detector(self) -> bool:
        """mediapipe Tasks API ile el algılayıcıyı kurar .
        Mümkünse VIDEO modu kullanılır (kareler arası takip → daha akıcı ve hızlı)."""
        tasks, vision = mp_python, mp_vision
        if tasks is None or vision is None:
            log.warning("[AirCanvas] mediapipe Tasks API yüklenemedi.")
            return False
        model_path = self.model_path or default_model_path()
        if not self._ensure_model_exists(model_path):
            log.warning("[AirCanvas] El modeli bulunamadı: %s", model_path)
            return False
        try:
            # Windows'ta Türkçe karakterli yollar veya dosya kilidi sorunlarını önlemek için buffer öncelikli yükleme
            try:
                with open(model_path, "rb") as fh:
                    model_bytes = fh.read()
                base_options = tasks.BaseOptions(model_asset_buffer=model_bytes)
            except Exception:
                base_options = tasks.BaseOptions(model_asset_path=model_path)

            common = dict(
                base_options=base_options,
                num_hands=1,
                min_hand_detection_confidence=0.7,
                min_hand_presence_confidence=0.7,
                min_tracking_confidence=0.7,
            )
            try:
                options = vision.HandLandmarkerOptions(running_mode=vision.RunningMode.VIDEO, **common)
                self.detector = vision.HandLandmarker.create_from_options(options)
                self._video_mode = True
            except Exception as exc:
                log.debug("[AirCanvas] VIDEO modu kullanılamadı, IMAGE moduna geçiliyor: %s", exc)
                options = vision.HandLandmarkerOptions(**common)
                self.detector = vision.HandLandmarker.create_from_options(options)
                self._video_mode = False
            return True
        except Exception as exc:
            log.error("[AirCanvas] HandLandmarker başlatılamadı: %s", exc)
            return False

    def _detect_hands(self, frame: np.ndarray) -> List[List[List[int]]]:
        """Her el için [[id, x, y], ...] listesi döndürür."""
        h, w = frame.shape[:2]
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        hands_lm: List[List[List[int]]] = []

        if self.detector is not None and mp is not None:
            try:
                mp_img = mp.Image(image_format=mp.ImageFormat.SRGB, data=np.ascontiguousarray(rgb_frame))
                if self._video_mode:
                    ts = max(int(time.monotonic() * 1000), self._last_ts_ms + 1)
                    self._last_ts_ms = ts
                    result = self.detector.detect_for_video(mp_img, ts)
                else:
                    result = self.detector.detect(mp_img)
            except Exception as exc:
                log.debug("[AirCanvas] El algılama hatası: %s", exc)
                return hands_lm
            for hand in (result.hand_landmarks or []):
                hands_lm.append([[i, int(lm.x * w), int(lm.y * h)] for i, lm in enumerate(hand)])
            return hands_lm

        if self.hands is not None:
            results = self.hands.process(rgb_frame)
            for hand_landmarks in (results.multi_hand_landmarks or []):
                hands_lm.append([[i, int(lm.x * w), int(lm.y * h)] for i, lm in enumerate(hand_landmarks.landmark)])
        return hands_lm

    @staticmethod
    def _fingers_up(lm: List[List[int]]) -> Dict[str, bool]:
        """Parmak ucu, orta ekleminden yukarıdaysa parmak 'açık' kabul edilir."""
        return {
            "index": lm[8][2] < lm[6][2],
            "middle": lm[12][2] < lm[10][2],
            "ring": lm[16][2] < lm[14][2],
            "pinky": lm[20][2] < lm[18][2],
        }

    def _process_hand(self, lm: List[List[int]]) -> None:
        """Bir karedeki el pozuna göre çizim / seçim / kalınlık modunu uygular."""
        if len(lm) < 21:
            return
        up = self._fingers_up(lm)
        ix, iy = lm[8][1], lm[8][2]

        if up["index"] and up["middle"] and up["ring"] and up["pinky"]:
            # Kalınlık modu: başparmak–işaret parmağı mesafesi fırça kalınlığını belirler
            if self._stroke_owner == "hand":
                self._end_stroke()
            dist = float(np.hypot(lm[4][1] - ix, lm[4][2] - iy))
            ratio = min(1.0, max(0.0, (dist - 25.0) / 175.0))
            self.brush_thickness = int(round(BRUSH_MIN + ratio * (BRUSH_MAX - BRUSH_MIN)))
            self._hand_overlay = (lm, "size")
            return

        if up["index"] and up["middle"]:
            # Seçim modu
            if self._stroke_owner == "hand":
                self._end_stroke()
            button_id = self._button_at(ix, iy)
            if button_id:
                self._press_button(button_id, from_gesture=True)
            self._hand_overlay = (lm, "select")
            return

        if up["index"]:
            # Çizim modu
            self._hand_overlay = (lm, "draw")
            if self._stroke_owner == "mouse":
                return
            if self._stroke_owner is None:
                self._begin_stroke((ix, iy), "hand")
                return
            if self._last_point is not None:
                px, py = self._last_point
                ix = int(px * SMOOTHING + ix * (1 - SMOOTHING))
                iy = int(py * SMOOTHING + iy * (1 - SMOOTHING))
            self._extend_stroke((ix, iy))
            return

        if self._stroke_owner == "hand":
            self._end_stroke()
        self._hand_overlay = (lm, "idle")

    def _on_hand_missing(self) -> None:
        """El kısa süre kaybolursa çizgi korunur; süre aşılırsa çizgi bitirilir."""
        self._missing_frames += 1
        self._hand_overlay = None
        if self._stroke_owner == "hand" and self._missing_frames >= HAND_LOST_GRACE_FRAMES:
            self._end_stroke()

    # ─── Çizim / arayüz ───────────────────────────────────────────────────────

    def _build_toolbar(self) -> List[Dict[str, Any]]:
        margin, gap = 10, 6
        count = len(TOOLBAR_IDS)
        width = (FRAME_W - 2 * margin - gap * (count - 1)) // count
        buttons, x = [], margin
        for button_id in TOOLBAR_IDS:
            buttons.append({"id": button_id, "x1": x, "x2": x + width})
            x += width + gap
        return buttons

    def _button_at(self, x: int, y: int) -> Optional[str]:
        if not (TOOLBAR_TOP <= y <= TOOLBAR_BOTTOM):
            return None
        for button in self.buttons:
            if button["x1"] <= x <= button["x2"]:
                return button["id"]
        return None

    @staticmethod
    def _text_color_for(bg) -> Tuple[int, int, int]:
        b, g, r = bg
        return (0, 0, 0) if (0.114 * b + 0.587 * g + 0.299 * r) > 140 else (255, 255, 255)

    def _button_style(self, button_id: str) -> Tuple[str, Tuple[int, int, int], bool]:
        """(etiket, arka plan rengi, aktif mi)"""
        if button_id.startswith("color_"):
            index = int(button_id.split("_", 1)[1])
            name, color = PALETTE[index]
            return name, color, (not self.eraser and self.color_index == index)
        styles = {
            "eraser": ("SILGI", (235, 235, 235), self.eraser),
            "shape": ("SEKIL:ON" if self.shape_assist else "SEKIL:OFF",
                      (60, 180, 75) if self.shape_assist else (100, 100, 100), False),
            "board": ("TAHTA" if self.board_mode else "KAMERA", (110, 80, 40), False),
            "clear": ("TEMIZLE", (60, 60, 180), False),
            "undo": ("GERI AL", (140, 90, 40), False),
            "save": ("KAYDET", (200, 200, 200), False),
            "voice": ("DINLIYOR" if self.voice_listening else "SESLI (V)",
                      (0, 140, 255) if self.voice_listening else (140, 45, 190), False),
        }
        return styles[button_id]

    @staticmethod
    def _put_centered(img, text, x1, y1, x2, y2, color, thickness=2) -> None:
        font = cv2.FONT_HERSHEY_SIMPLEX
        scale = 0.55
        (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
        while tw > (x2 - x1) - 8 and scale > 0.3:
            scale -= 0.05
            (tw, th), _ = cv2.getTextSize(text, font, scale, thickness)
        org = (x1 + ((x2 - x1) - tw) // 2, y1 + ((y2 - y1) + th) // 2)
        cv2.putText(img, text, org, font, scale, color, thickness)

    def _draw_toolbar(self, img: np.ndarray) -> None:
        for button in self.buttons:
            label, bg, active = self._button_style(button["id"])
            fg = self._text_color_for(bg)
            x1, x2 = button["x1"], button["x2"]
            cv2.rectangle(img, (x1, TOOLBAR_TOP), (x2, TOOLBAR_BOTTOM), bg, cv2.FILLED)
            if active:
                cv2.rectangle(img, (x1 + 3, TOOLBAR_TOP + 3), (x2 - 3, TOOLBAR_BOTTOM - 3), fg, 3)
            self._put_centered(img, label, x1, TOOLBAR_TOP, x2, TOOLBAR_BOTTOM, fg)

    def _compose(self, frame: np.ndarray) -> np.ndarray:
        """Tuvali kamera görüntüsü (veya tahta) üzerine opak şekilde yerleştirir."""
        view = np.full_like(frame, BOARD_BG) if self.board_mode else frame.copy()
        if self.design is not None and self.show_guide and self._guide is not None:
            # 3D rehber kullanıcının çizgilerinin altında, yarı saydam durur (üzerinden çizilebilir).
            guide, gmask = self._guide
            x1, y1, x2, y2 = GUIDE_RECT
            region = view[y1:y2, x1:x2]
            region[gmask] = (region[gmask] * (1 - GUIDE_ALPHA) + guide[gmask] * GUIDE_ALPHA).astype(np.uint8)
        mask = self.canvas.any(axis=2)
        view[mask] = self.canvas[mask]
        return view

    def _draw_hand_overlay(self, img: np.ndarray) -> None:
        if not self._hand_overlay:
            return
        lm, mode = self._hand_overlay
        for p1, p2 in HAND_CONNECTIONS:
            cv2.line(img, (lm[p1][1], lm[p1][2]), (lm[p2][1], lm[p2][2]), (0, 200, 100), 2)
        for _, cx, cy in lm:
            cv2.circle(img, (cx, cy), 3, (0, 255, 255), cv2.FILLED)

        ix, iy = lm[8][1], lm[8][2]
        if mode == "draw":
            radius = max(4, self._current_thickness() // 2)
            if self.eraser:
                cv2.circle(img, (ix, iy), radius, (255, 255, 255), 2)
            else:
                cv2.circle(img, (ix, iy), radius, self._current_color(), cv2.FILLED)
                cv2.circle(img, (ix, iy), radius + 2, (255, 255, 255), 1)
        elif mode == "select":
            mx, my = lm[12][1], lm[12][2]
            cv2.rectangle(img, (min(ix, mx), min(iy, my) - 25), (max(ix, mx), max(iy, my) + 25), (255, 0, 255), 2)
        elif mode == "size":
            tx, ty = lm[4][1], lm[4][2]
            cv2.line(img, (tx, ty), (ix, iy), (255, 255, 255), 2)
            mid = ((tx + ix) // 2, (ty + iy) // 2)
            cv2.circle(img, mid, max(2, self.brush_thickness // 2), self.colors[self.color_index], cv2.FILLED)
            cv2.putText(img, f"KALINLIK {self.brush_thickness}", (mid[0] + 20, mid[1]),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 255), 2)

    def _draw_hud(self, img: np.ndarray, fps: float) -> None:
        # Sağ üst durum çipi (her zaman görünür)
        tool = "SILGI" if self.eraser else PALETTE[self.color_index][0]
        mode = " | CEVRIMDISI" if self.offline else (" | AI: YEREL" if self.ai_mode == "local" else "")
        status = (f"{tool} | {self._current_thickness()}px | {'TAHTA' if self.board_mode else 'KAMERA'}"
                  f"{mode} | {fps:4.1f} FPS")
        (tw, th), _ = cv2.getTextSize(status, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        x2, y1 = FRAME_W - 12, DRAW_MIN_Y + 4
        cv2.rectangle(img, (x2 - tw - 16, y1), (x2, y1 + th + 14), (20, 24, 38), cv2.FILLED)
        cv2.putText(img, status, (x2 - tw - 8, y1 + th + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1)

        if self._last_shape_label and (time.time() - self._last_shape_time < 3.0):
            cv2.putText(img, self._last_shape_label, (24, DRAW_MIN_Y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 255, 120), 2)

        if self.design is not None:
            self._draw_design_panel(img)
        self._draw_answer_box(img)

        if not self.show_hud:
            return

        roi = img[HUD_TOP:FRAME_H - 8, 10:FRAME_W - 10]
        panel = np.full_like(roi, (38, 24, 20))
        cv2.addWeighted(panel, 0.78, roi, 0.22, 0, dst=roi)

        tip_ascii = self._tr_ascii(self.assistant_tip)
        summary_ascii = self._tr_ascii(self.voice_summary)
        busy = " ..." if (self.design_busy or self.ai_busy) and int(time.time() * 2) % 2 else ""
        cv2.putText(img, f"ASISTAN: {tip_ascii[:105]}{busy}", (22, HUD_TOP + 26), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (0, 255, 200), 2)
        cv2.putText(img, f"SESLI NOT: {summary_ascii[:105]}", (22, HUD_TOP + 52), cv2.FONT_HERSHEY_SIMPLEX, 0.56, (255, 220, 90), 2)
        hints = (
            "[V] Sesli komut  |  [Z/Y] Geri/Ileri  |  [C] Temizle  |  [S] Kaydet  |  [B] Tahta  |  [E] Silgi  |  "
            "[+/-] Kalinlik  |  [1-6] Renk  |  [H] Panel  |  [Q] Cikis",
            "3D: 'dron tasarla', 'M3 vida ciz'...  |  [N/P] Sonraki/Onceki adim  |  [R] Tekrar  |  [G] Rehber  |  "
            "[T] Taslak  |  [X] STL  |  [K] Kontrol et  |  [F] Tam ekran",
        )
        for i, line in enumerate(hints):
            cv2.putText(img, line, (22, HUD_TOP + 76 + i * 18), cv2.FONT_HERSHEY_SIMPLEX, 0.44, (210, 215, 230), 1)

    @staticmethod
    def _wrap(text: str, width: int) -> List[str]:
        lines: List[str] = []
        for paragraph in (text or "").splitlines():
            current = ""
            for word in paragraph.split():
                if current and len(current) + 1 + len(word) > width:
                    lines.append(current)
                    current = word
                else:
                    current = f"{current} {word}".strip()
            if current:
                lines.append(current)
        return lines

    def _draw_answer_box(self, img: np.ndarray) -> None:
        """AI cevabını HUD'un hemen üstünde, satırlara bölünmüş yarı saydam kutuda gösterir."""
        if not self._answer_text or time.time() > self._answer_until:
            return
        x1 = 20
        x2 = GUIDE_RECT[2] if self.design is not None else FRAME_W - 20
        font, scale = cv2.FONT_HERSHEY_SIMPLEX, 0.52
        max_px = x2 - x1 - 24
        # Yazı tipi genişliği OpenCV sürümüne göre değişir; satırları piksel genişliğine göre böl.
        lines: List[str] = []
        for paragraph in self._tr_ascii(self._answer_text).splitlines():
            current = ""
            for word in paragraph.split():
                trial = f"{current} {word}".strip()
                if current and cv2.getTextSize(trial, font, scale, 1)[0][0] > max_px:
                    lines.append(current)
                    current = word
                else:
                    current = trial
            if current:
                lines.append(current)
        if not lines:
            return
        if len(lines) > 6:
            lines = lines[:6]
            lines[-1] = lines[-1].rsplit(" ", 1)[0] + " ..."
        y2 = HUD_TOP - 10
        y1 = max(DRAW_MIN_Y + 40, y2 - 14 - 22 * len(lines))
        roi = img[y1:y2, x1:x2]
        cv2.addWeighted(np.full_like(roi, (48, 32, 16)), 0.8, roi, 0.2, 0, dst=roi)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 220, 180), 1)
        for i, line in enumerate(lines):
            cv2.putText(img, line, (x1 + 12, y1 + 24 + 22 * i), font, scale,
                        (235, 245, 240), 1, cv2.LINE_AA)

    def _draw_design_panel(self, img: np.ndarray) -> None:
        """Sağ tarafta tasarım adı, parça listesi ve geçerli adımın talimatı."""
        design = self.design
        if design is None:
            return
        x1, y1, x2, y2 = PANEL_RECT
        roi = img[y1:y2, x1:x2]
        cv2.addWeighted(np.full_like(roi, (36, 26, 22)), 0.82, roi, 0.18, 0, dst=roi)
        cv2.rectangle(img, (x1, y1), (x2, y2), (0, 200, 255), 1)
        font = cv2.FONT_HERSHEY_SIMPLEX
        y = y1 + 24
        cv2.putText(img, self._tr_ascii(design.name)[:30], (x1 + 10, y), font, 0.62, (0, 220, 255), 2)
        y += 8
        for i, part in enumerate(design.parts):
            y += 20
            if y > y1 + 190:
                cv2.putText(img, "...", (x1 + 14, y), font, 0.45, (200, 200, 200), 1)
                break
            active = i == self.design_step
            qty = design.quantity(i)
            label = f"{i + 1}. {self._tr_ascii(part['name'])}" + (f" x{qty}" if qty > 1 else "")
            cv2.putText(img, ("> " if active else "  ") + label[:34], (x1 + 10, y), font, 0.47,
                        (40, 200, 255) if active else (190, 190, 190), 2 if active else 1)
        y += 14
        cv2.line(img, (x1 + 10, y), (x2 - 10, y), (90, 90, 90), 1)
        body = (design.step_text(self.design_step) if self.design_step >= 0
                else f"Genel gorunum.\n{design.summary}")
        for line in self._wrap(self._tr_ascii(body), 45):
            y += 19
            if y > y2 - 8:
                break
            cv2.putText(img, line, (x1 + 10, y), font, 0.45, (235, 235, 235), 1)

    @staticmethod
    def _wheel_delta(flags: int) -> int:
        """Fare tekerleği adımı (işaretli). OpenCV 5 `getMouseWheelDelta`'yı kaldırdı;
        değer `flags`'in üst 16 bitinde işaretli tamsayı olarak gelir."""
        getter = getattr(cv2, "getMouseWheelDelta", None)
        if getter is not None:
            return int(getter(flags))
        delta = (int(flags) >> 16) & 0xFFFF
        return delta - 0x10000 if delta >= 0x8000 else delta

    def _on_mouse(self, event, x, y, flags, param) -> None:
        if event == cv2.EVENT_LBUTTONDOWN:
            button_id = self._button_at(x, y)
            if button_id:
                self._press_button(button_id)
                return
            if self._stroke_owner == "hand":
                self._end_stroke()
            self._begin_stroke((x, y), "mouse")
        elif event == cv2.EVENT_MOUSEMOVE and self._stroke_owner == "mouse":
            self._extend_stroke((x, y))
        elif event == cv2.EVENT_LBUTTONUP and self._stroke_owner == "mouse":
            self._end_stroke()
        elif event == cv2.EVENT_MOUSEWHEEL:
            delta = self._wheel_delta(flags)
            if delta == 0:
                return
            self._perform("brush_up" if delta > 0 else "brush_down")

    # ─── Ana döngü ────────────────────────────────────────────────────────────

    def run(self) -> str:
        """Kamerayı açar ve çizim döngüsünü çalıştırır (bloklar). Çıkmak için 'q'/ESC veya pencereyi kapatın."""
        with self._run_lock:
            if self._running:
                return "Air Canvas zaten açık."
            self._running = True
        self._stop_event.clear()
        try:
            return self._run_loop()
        finally:
            self._running = False

    def _reset_session(self) -> None:
        self.canvas[:] = 0
        self.undo_stack.clear()
        self.redo_stack.clear()
        self.voice_notes.clear()
        self._stroke_owner = None
        self._stroke_base = None
        self._stroke_points = []
        self._last_point = None
        self._missing_frames = 0
        self._hand_overlay = None
        self._last_view = None
        self._session_saves = 0
        while not self._actions.empty():
            try:
                self._actions.get_nowait()
            except queue.Empty:
                break

    def _apply_window_mode(self, win_name: str) -> None:
        self._window_dirty = False
        try:
            if self.fullscreen:
                cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_FULLSCREEN)
            else:
                cv2.setWindowProperty(win_name, cv2.WND_PROP_FULLSCREEN, cv2.WINDOW_NORMAL)
                cv2.resizeWindow(win_name, FRAME_W, FRAME_H)
        except cv2.error as exc:
            log.debug("[AirCanvas] Pencere modu değiştirilemedi: %s", exc)

    def _run_loop(self) -> str:
        if not self.available:
            self._try_init()
        if not self.available:
            return self._unavailable_message()

        cam = self.camera_index
        cap = cv2.VideoCapture(cam, cv2.CAP_DSHOW) if os.name == "nt" else cv2.VideoCapture(cam)
        if not cap.isOpened():
            cap = cv2.VideoCapture(cam)
        if not cap.isOpened():
            log.error("[AirCanvas] Kamera açılamadı (başka bir uygulama kullanıyor olabilir).")
            return "Kamera açılamadı. Lütfen kameranın başka bir pencere tarafından kullanılmadığından emin olun."

        cap.set(cv2.CAP_PROP_FRAME_WIDTH, FRAME_W)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, FRAME_H)
        self._reset_session()
        if self._pending_design:
            self._actions.put(("design_request", self._pending_design))
            self._pending_design = ""

        win_name = "Air Canvas"
        # WINDOW_NORMAL: pencere ölçeklenebilir; OpenCV fare koordinatlarını görüntüye geri ölçekler.
        cv2.namedWindow(win_name, cv2.WINDOW_NORMAL)
        self._apply_window_mode(win_name)
        cv2.setMouseCallback(win_name, self._on_mouse)

        log.info("[AirCanvas] Çizim modu başlatıldı. Çıkış: Q/ESC, Sesli not: V, Geri al: Z.")
        if self.offline:
            log.info("[AirCanvas] Çevrimdışı mod: yerel LLM, Whisper ve yerel ses; internete veri gitmez.")
            preload = getattr(self._get_listener(), "preload_async", None)
            if callable(preload):
                preload()
        self._say_async("Air Canvas çizim asistanı hazır.")

        fps, last_t, read_failures = 0.0, time.time(), 0
        try:
            while not self._stop_event.is_set():
                success, frame = cap.read()
                if not success:
                    read_failures += 1
                    if read_failures > 30:
                        log.error("[AirCanvas] Kameradan görüntü alınamıyor, çizim modu kapatılıyor.")
                        break
                    continue
                read_failures = 0

                frame = cv2.flip(frame, 1)  # Ayna görüntüsü
                if frame.shape[0] != FRAME_H or frame.shape[1] != FRAME_W:
                    frame = cv2.resize(frame, (FRAME_W, FRAME_H))

                hands_lm = self._detect_hands(frame)
                if hands_lm:
                    self._missing_frames = 0
                    self._process_hand(hands_lm[0])
                else:
                    self._on_hand_missing()

                self._drain_actions()
                if self._window_dirty:
                    self._apply_window_mode(win_name)

                view = self._compose(frame)
                self._draw_hand_overlay(view)
                self._draw_toolbar(view)

                now = time.time()
                dt = now - last_t
                last_t = now
                if dt > 0:
                    fps = fps * 0.9 + (1.0 / dt) * 0.1 if fps else 1.0 / dt
                self._draw_hud(view, fps)
                self._last_view = view

                cv2.imshow(win_name, view)
                key = cv2.waitKey(1)
                if key != -1:
                    key &= 0xFF
                    if key in (ord('q'), ord('Q'), 27):
                        break
                    self._handle_key(key)

                try:
                    if cv2.getWindowProperty(win_name, cv2.WND_PROP_VISIBLE) < 1:
                        break  # Pencere X ile kapatıldı
                except cv2.error:
                    break
        finally:
            if self._stroke_owner:
                self._end_stroke(allow_snap=False)
            cap.release()
            try:
                cv2.destroyWindow(win_name)
                cv2.waitKey(1)
            except cv2.error:
                pass

        if self._session_saves:
            return (f"Çizim modu kapatıldı. {self._session_saves} çizim kaydedildi "
                    f"(son: {os.path.basename(self.last_saved_path)}, klasör: {self.save_dir}).")
        return "Çizim modu kapatıldı. Kaydetmek isterseniz bir dahaki sefere S tuşunu veya KAYDET butonunu kullanın."
