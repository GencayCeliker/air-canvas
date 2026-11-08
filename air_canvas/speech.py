"""Türkçe sesli geri bildirim: Edge TTS (nöral) → gTTS → Türkçe Windows sesi.

Hiçbiri yoksa sessiz kalır; İngilizce bir sesle bozuk Türkçe okumaktan iyidir.
Tüm bağımlılıklar isteğe bağlıdır (pip install "air-canvas[voice]").
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


class TurkishSpeaker:
    """``say()`` engellemez; konuşma sürerken gelen yeni mesajlar atlanır."""

    def __init__(self, voice: str = EDGE_VOICE):
        self.voice = voice
        self._busy = threading.Event()

    def is_busy(self) -> bool:
        return self._busy.is_set()

    def say(self, text: str) -> None:
        text = (text or "").strip()
        if not text or self._busy.is_set():
            return
        self._busy.set()
        threading.Thread(target=self._speak, args=(text,), daemon=True, name="AirCanvasTTS").start()

    # ─── İç işler ─────────────────────────────────────────────────────────────

    def _speak(self, text: str) -> None:
        try:
            path = self._synthesize(text)
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
        try:
            from gtts import gTTS  # type: ignore

            gTTS(text=text, lang="tr").save(path)
            if os.path.getsize(path) > 0:
                return path
        except Exception as exc:
            log.debug("[AirCanvas] gTTS kullanılamadı: %s", exc)
        _remove_quiet(path)
        return None

    @staticmethod
    def _play_mp3(path: str) -> bool:
        try:
            import pygame  # type: ignore

            if not pygame.mixer.get_init():
                pygame.mixer.init()
            pygame.mixer.music.load(path)
            pygame.mixer.music.play()
            while pygame.mixer.music.get_busy():
                time.sleep(0.05)
            pygame.mixer.music.unload()
            return True
        except Exception as exc:
            log.debug("[AirCanvas] MP3 çalınamadı: %s", exc)
            return False

    @staticmethod
    def _speak_sapi_turkish(text: str) -> None:
        if os.name != "nt":
            return
        com_initialized = False
        try:
            import comtypes  # type: ignore
            import comtypes.client  # type: ignore

            comtypes.CoInitialize()
            com_initialized = True
            sapi = comtypes.client.CreateObject("SAPI.SpVoice")
            voices = sapi.GetVoices()
            for i in range(voices.Count):
                v = voices.Item(i)
                desc = str(v.GetDescription()).lower()
                if "tolga" in desc or "turkish" in desc or "türkçe" in desc or "tr-tr" in desc:
                    sapi.Voice = v
                    sapi.Speak(text)
                    return
            log.info("[AirCanvas] Türkçe ses bulunamadı; sesli geri bildirim atlandı.")
        except Exception as exc:
            log.debug("[AirCanvas] SAPI kullanılamadı: %s", exc)
        finally:
            if com_initialized:
                try:
                    comtypes.CoUninitialize()
                except Exception:
                    pass


def _remove_quiet(path: Optional[str]) -> None:
    if path:
        try:
            os.remove(path)
        except OSError:
            pass
