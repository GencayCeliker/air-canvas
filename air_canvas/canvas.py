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

from .config import default_model_path, default_save_dir

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
}


def _spoken(label: str) -> str:
    return SPOKEN_NAMES.get(label, label.lower())


VOICE_SHAPES = [
    (("daire", "çember", "cember", "yuvarlak"), "DAIRE"),
    (("kare", "dikdörtgen", "dikdortgen", "kutu"), "KARE"),
    (("üçgen", "ucgen"), "UCGEN"),
]


class AirCanvas:
    def __init__(
        self,
        auto_init: bool = True,
        speaker=None,
        llm=None,
        save_dir: Optional[str] = None,
        camera_index: int = 0,
        model_path: Optional[str] = None,
    ):
        """
        speaker: ``say(text)`` ve ``is_busy()`` metotları olan bir ses motoru.
                 Verilmezse ilk ihtiyaçta yerleşik Türkçe motor (Edge TTS → gTTS) kurulur.
        llm:     ``generate(prompt, system=..., temperature=..., max_tokens=...)`` ve
                 ``available`` özelliği olan bir istemci. Verilmezse LM Studio / Ollama denenir.
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
        self._run_lock = threading.Lock()
        self._running = False
        self._stop_event = threading.Event()
        self._llm = llm
        self._llm_checked_at = 0.0
        self._last_view: Optional[np.ndarray] = None
        self._last_shape_label = ""
        self._last_shape_time = 0.0
        self._session_saves = 0

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
                self._speaker = TurkishSpeaker()
            except Exception as exc:
                log.warning("[AirCanvas] Türkçe ses motoru başlatılamadı: %s", exc)
                self._speaker_failed = True
        return self._speaker

    def _say_async(self, text: str) -> None:
        """Kamera FPS'ini düşürmeden arka planda Türkçe sesli geri bildirim verir.
        Zaten konuşuluyorsa yeni mesaj atlanır (sesler üst üste binmez)."""
        if not self.voice_feedback or not text:
            return

        speaker = self._get_speaker()
        if speaker is not None:
            try:
                if not speaker.is_busy():
                    speaker.say(text)
                return
            except Exception as exc:
                log.warning("[AirCanvas] Ses motoru hatası: %s", exc)

        threading.Thread(target=self._say_sapi_turkish, args=(text,), daemon=True).start()

    def _say_sapi_turkish(self, text: str) -> None:
        """Son çare: yalnızca Türkçe bir Windows sesi kuruluysa SAPI ile konuşur.
        Türkçe ses yoksa susar; İngilizce sesle bozuk Türkçe okumaktan iyidir."""
        if not self._speech_lock.acquire(blocking=False):
            return
        com_initialized = False
        try:
            import comtypes  # type: ignore
            import comtypes.client  # type: ignore
            comtypes.CoInitialize()
            com_initialized = True
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
            if com_initialized:
                try:
                    comtypes.CoUninitialize()
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

        if "hepsini sil" in low or "tuvali sil" in low or has("temizle"):
            return [("clear", None)], "Tuval temizlendi."
        if "geri al" in low or has("iptal"):
            return [("undo", None)], "Son adım geri alındı."
        if "ileri al" in low or has("yinele"):
            return [("redo", None)], "Adım yinelendi."
        if has("kaydet"):
            return [("save", None)], "Çizim kaydediliyor."

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

        for keywords, index in VOICE_COLORS:
            if has(*keywords):
                actions.append(("color", index))
                messages.append(f"Kalem rengi {_spoken(PALETTE[index][0])} yapıldı")
                break

        for keywords, shape in VOICE_SHAPES:
            if has(*keywords):
                actions.append(("stamp", shape))
                messages.append(f"Merkeze {_spoken(shape)} çizildi")
                break

        if not actions:
            return [], "Sesli notunuz kaydedildi."
        return actions, ". ".join(messages) + "."

    def _listen_and_summarize_async(self) -> None:
        """Mikrofonu dinler; komutsa eylem kuyruğuna ekler, değilse not olarak özetler."""
        if self.voice_listening:
            return
        self.voice_listening = True
        self.assistant_tip = "Asistan dinliyor... Lutfen konusun (4 sn)"

        def _worker():
            try:
                spoken_text = ""
                try:
                    import speech_recognition as sr  # type: ignore
                    recognizer = sr.Recognizer()
                    with sr.Microphone() as source:
                        recognizer.adjust_for_ambient_noise(source, duration=0.3)
                        audio = recognizer.listen(source, timeout=4, phrase_time_limit=6)
                    spoken_text = recognizer.recognize_google(audio, language="tr-TR")
                except Exception as exc:
                    log.debug("[AirCanvas] SpeechRecognition dinleme uyarısı: %s", exc)

                if not spoken_text:
                    self.assistant_tip = "Ses algilanamadi. Tekrar denemek icin 'V' tusuna basin."
                    self._say_async("Sesinizi duyamadım, lütfen tekrar söyleyin.")
                    return

                self.voice_raw = spoken_text
                actions, message = self._parse_voice_command(spoken_text)
                if actions:
                    for action in actions:
                        self._actions.put(action)
                    self.assistant_tip = message
                    self._say_async(message)
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
            self._stamp_shape(str(value))
        elif action == "save":
            self._save()
        elif action == "voice":
            self._listen_and_summarize_async()

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
        if char in key_actions:
            self._perform(key_actions[char])
        elif char == "e":
            self._perform("pen" if self.eraser else "eraser")
        elif char.isdigit() and 1 <= int(char) <= len(PALETTE):
            self._perform("color", int(char) - 1)

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

    def _stamp_shape(self, name: str) -> None:
        """Sesli komutla tuvalin ortasına hazır şekil çizer."""
        cx, cy = FRAME_W // 2, (DRAW_MIN_Y + HUD_TOP) // 2
        color = self.colors[self.color_index]
        if name == "DAIRE":
            shape = {"name": name, "kind": "circle", "center": (cx, cy), "radius": 130}
        elif name == "KARE":
            shape = {"name": name, "kind": "poly", "points": np.array(
                [[cx - 130, cy - 130], [cx + 130, cy - 130], [cx + 130, cy + 130], [cx - 130, cy + 130]], np.int32)}
        elif name == "UCGEN":
            shape = {"name": name, "kind": "poly", "points": np.array(
                [[cx, cy - 130], [cx - 150, cy + 120], [cx + 150, cy + 120]], np.int32)}
        else:
            return
        self._push_undo()
        self._draw_shape(self.canvas, shape, color, self.brush_thickness)

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

        if MP_OK:
            try:
                self.mp_hands = mp.solutions.hands
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
        model_path = self.model_path or default_model_path()
        if not self._ensure_model_exists(model_path):
            log.warning("[AirCanvas] El modeli bulunamadı: %s", model_path)
            return False
        try:
            # Windows'ta Türkçe karakterli yollar veya dosya kilidi sorunlarını önlemek için buffer öncelikli yükleme
            try:
                with open(model_path, "rb") as fh:
                    model_bytes = fh.read()
                base_options = mp_python.BaseOptions(model_asset_buffer=model_bytes)
            except Exception:
                base_options = mp_python.BaseOptions(model_asset_path=model_path)

            common = dict(
                base_options=base_options,
                num_hands=1,
                min_hand_detection_confidence=0.7,
                min_hand_presence_confidence=0.7,
                min_tracking_confidence=0.7,
            )
            try:
                options = mp_vision.HandLandmarkerOptions(running_mode=mp_vision.RunningMode.VIDEO, **common)
                self.detector = mp_vision.HandLandmarker.create_from_options(options)
                self._video_mode = True
            except Exception as exc:
                log.debug("[AirCanvas] VIDEO modu kullanılamadı, IMAGE moduna geçiliyor: %s", exc)
                options = mp_vision.HandLandmarkerOptions(**common)
                self.detector = mp_vision.HandLandmarker.create_from_options(options)
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

        if self.detector is not None:
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
        status = f"{tool} | {self._current_thickness()}px | {'TAHTA' if self.board_mode else 'KAMERA'} | {fps:4.1f} FPS"
        (tw, th), _ = cv2.getTextSize(status, cv2.FONT_HERSHEY_SIMPLEX, 0.5, 1)
        x2, y1 = FRAME_W - 12, DRAW_MIN_Y + 4
        cv2.rectangle(img, (x2 - tw - 16, y1), (x2, y1 + th + 14), (20, 24, 38), cv2.FILLED)
        cv2.putText(img, status, (x2 - tw - 8, y1 + th + 6), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (230, 230, 230), 1)

        if self._last_shape_label and (time.time() - self._last_shape_time < 3.0):
            cv2.putText(img, self._last_shape_label, (24, DRAW_MIN_Y + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.7, (80, 255, 120), 2)

        if not self.show_hud:
            return

        roi = img[HUD_TOP:FRAME_H - 8, 10:FRAME_W - 10]
        panel = np.full_like(roi, (38, 24, 20))
        cv2.addWeighted(panel, 0.78, roi, 0.22, 0, dst=roi)

        tip_ascii = self._tr_ascii(self.assistant_tip)
        summary_ascii = self._tr_ascii(self.voice_summary)
        cv2.putText(img, f"ASISTAN: {tip_ascii[:105]}", (22, HUD_TOP + 30), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (0, 255, 200), 2)
        cv2.putText(img, f"SESLI NOT: {summary_ascii[:105]}", (22, HUD_TOP + 62), cv2.FONT_HERSHEY_SIMPLEX, 0.58, (255, 220, 90), 2)
        cv2.putText(
            img,
            "[V] Sesli | [Z/Y] Geri/Ileri | [C] Temizle | [S] Kaydet | [B] Tahta | [E] Silgi | [+/-] Kalinlik | [1-6] Renk | [H] Panel | [Q] Cikis",
            (22, HUD_TOP + 92),
            cv2.FONT_HERSHEY_SIMPLEX,
            0.47,
            (210, 215, 230),
            1,
        )

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
            delta = cv2.getMouseWheelDelta(flags)
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

        win_name = "Air Canvas"
        cv2.namedWindow(win_name)
        cv2.setMouseCallback(win_name, self._on_mouse)

        log.info("[AirCanvas] Çizim modu başlatıldı. Çıkış: Q/ESC, Sesli not: V, Geri al: Z.")
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
