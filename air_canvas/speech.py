"""Türkçe sesli geri bildirim: Edge TTS (nöral) → gTTS → Türkçe Windows sesi.

Hiçbiri yoksa sessiz kalır; İngilizce bir sesle bozuk Türkçe okumaktan iyidir.
Tüm bağımlılıklar isteğe bağlıdır (pip install "air-canvas[voice]").

``online=False`` (çevrimdışı mod) iken metin internete gönderilmez: yalnızca
kurulu Türkçe Windows sesi kullanılır.
"""

import asyncio
import logging
import os
import tempfile
import threading
import time
from typing import Optional

log = logging.getLogger(__name__)

EDGE_VOICE = os.getenv("AIR_CANVAS_VOICE", "tr-TR-AhmetNeural")  # alternatif: tr-TR-EmelNeural

# SAPI bayrakları: eşzamansız konuş / kuyruğu boşaltıp sustur
_SVSF_ASYNC = 1
_SVSF_PURGE = 2


class TurkishSpeaker:
    """``say()`` engellemez; konuşma sürerken gelen yeni mesajlar atlanır.
    ``stop()`` süren konuşmayı keser (ör. mikrofon dinlemeye başlamadan önce)."""

    def __init__(self, voice: str = EDGE_VOICE, online: bool = True):
        self.voice = voice
        self.online = online
        self._busy = threading.Event()
        self._cancel = threading.Event()

    def is_busy(self) -> bool:
        return self._busy.is_set()

    def say(self, text: str) -> None:
        text = (text or "").strip()
        if not text or self._busy.is_set():
            return
        self._cancel.clear()
        self._busy.set()
        threading.Thread(target=self._speak, args=(text,), daemon=True, name="AirCanvasTTS").start()

    def stop(self) -> None:
        """Süren konuşmayı keser; sentez sürüyorsa sonucu çalınmaz."""
        if not self._busy.is_set():
            return
        self._cancel.set()
        try:
            import pygame  # type: ignore

            if pygame.mixer.get_init():
                pygame.mixer.music.stop()
        except Exception:
            pass

    # ─── İç işler ─────────────────────────────────────────────────────────────

    def _speak(self, text: str) -> None:
        try:
            path = self._synthesize(text) if self.online else None
            if self._cancel.is_set():
                _remove_quiet(path)
                return
            if path:
                try:
                    if self._play_mp3(path):
                        return
                finally:
                    _remove_quiet(path)
            self._speak_sapi_turkish(text)
        except Exception as exc:
            log.debug("[AirCanvas] Konuşma hatası: %s", exc)
        finally:
            self._busy.clear()

    def _synthesize(self, text: str) -> Optional[str]:
        fd, path = tempfile.mkstemp(prefix="air_canvas_", suffix=".mp3")
        os.close(fd)
        try:
            import edge_tts  # type: ignore

            asyncio.run(edge_tts.Communicate(text, self.voice).save(path))
            if os.path.getsize(path) > 0:
                return path
        except Exception as exc:
            log.debug("[AirCanvas] Edge TTS kullanılamadı: %s", exc)
        if self._cancel.is_set():
            _remove_quiet(path)
            return None
        try:
            from gtts import gTTS  # type: ignore

            gTTS(text=text, lang="tr").save(path)
            if os.path.getsize(path) > 0:
                return path
        except Exception as exc:
            log.debug("[AirCanvas] gTTS kullanılamadı: %s", exc)
        _remove_quiet(path)
        return None

    def _play_mp3(self, path: str) -> bool:
        try:
            import pygame  # type: ignore

            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                if self._cancel.is_set():
                    pygame.mixer.music.stop()
                    break
                time.sleep(0.05)
            pygame.mixer.music.unload()
            return True
        except Exception as exc:
            log.debug("[AirCanvas] MP3 çalınamadı: %s", exc)
            return False

    def _speak_sapi_turkish(self, text: str) -> None:
        if os.name != "nt":
            return
        com = None   # CoInitialize başarılıysa comtypes modülü; finally'de CoUninitialize için
        try:
            import comtypes  # type: ignore
            import comtypes.client  # type: ignore

            comtypes.CoInitialize()
            com = comtypes
            sapi = comtypes.client.CreateObject("SAPI.SpVoice")
            voice = find_turkish_voice(sapi, comtypes.client)
            if voice is None:
                log.info("[AirCanvas] Türkçe Windows sesi bulunamadı; sesli geri bildirim atlandı. "
                         "Ayarlar > Saat ve dil > Konuşma bölümünden Türkçe ses ekleyebilirsiniz.")
                return
            sapi.Voice = voice
            sapi.Speak(text, _SVSF_ASYNC)
            # Eşzamansız konuşup iptali yokla: stop() SAPI sesini de keser.
            while not sapi.WaitUntilDone(100):
                if self._cancel.is_set():
                    sapi.Speak("", _SVSF_ASYNC | _SVSF_PURGE)
                    break
        except Exception as exc:
            log.debug("[AirCanvas] SAPI kullanılamadı: %s", exc)
        finally:
            if com is not None:
                try:
                    com.CoUninitialize()
                except Exception:
                    pass


# Windows 10/11'de Türkçe ses (Microsoft Tolga) çoğu zaman yalnızca OneCore kataloğundadır;
# SAPI'nin GetVoices() listesi bunu göstermez, ama kategori kimliği verilince kullanılabilir.
_ONECORE_VOICES = r"HKEY_LOCAL_MACHINE\SOFTWARE\Microsoft\Speech_OneCore\Voices"
_TURKISH_HINTS = ("tolga", "turkish", "türkçe", "turkce", "tr-tr", "trtr")


def _is_turkish(description: str) -> bool:
    desc = description.lower()
    return any(h in desc for h in _TURKISH_HINTS)


def find_turkish_voice(sapi, comtypes_client):
    """SAPI'de Türkçe bir ses jetonu arar: önce klasik liste, sonra OneCore sesleri. Yoksa None."""
    try:
        voices = sapi.GetVoices()
        for i in range(voices.Count):
            v = voices.Item(i)
            if _is_turkish(str(v.GetDescription())):
                return v
    except Exception as exc:
        log.debug("[AirCanvas] SAPI ses listesi okunamadı: %s", exc)
    try:
        category = comtypes_client.CreateObject("SAPI.SpObjectTokenCategory")
        category.SetId(_ONECORE_VOICES, False)
        tokens = category.EnumerateTokens()
        for i in range(tokens.Count):
            token = tokens.Item(i)
            if _is_turkish(str(token.GetDescription())):
                return token
    except Exception as exc:
        log.debug("[AirCanvas] OneCore sesleri okunamadı: %s", exc)
    return None


def _remove_quiet(path: Optional[str]) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
