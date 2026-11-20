"""Ses kuyruğu, konuşmayı kesme ve sesli komut testleri — mikrofon / hoparlör gerektirmez."""

import threading
import time

import pytest

pytest.importorskip("cv2")

from air_canvas import canvas as ac  # noqa: E402


class SlowSpeaker:
    """Her mesajı kısa bir süre 'konuşan' sahte ses motoru."""

    def __init__(self, duration=0.05):
        self.said = []
        self.stopped = 0
        self._busy = threading.Event()
        self._duration = duration

    def is_busy(self):
        return self._busy.is_set()

    def say(self, text):
        if self._busy.is_set():
            return
        self.said.append(text)
        self._busy.set()
        threading.Timer(self._duration, self._busy.clear).start()

    def stop(self):
        self.stopped += 1
        self._busy.clear()


def _wait_until(cond, timeout=3.0):
    deadline = time.time() + timeout
    while not cond() and time.time() < deadline:
        time.sleep(0.01)
    return cond()


def test_waited_messages_are_spoken_in_order_without_drops():
    speaker = SlowSpeaker()
    c = ac.AirCanvas(auto_init=False, speaker=speaker)
    for text in ("Dron hazır.", "Adım 1: alt plaka.", "Duvar 2 milimetre olsun."):
        c._say_async(text, wait=True)
    assert _wait_until(lambda: len(speaker.said) == 3)
    assert speaker.said == ["Dron hazır.", "Adım 1: alt plaka.", "Duvar 2 milimetre olsun."]


def test_stop_speech_interrupts_and_discards_queue():
    speaker = SlowSpeaker(duration=0.5)
    c = ac.AirCanvas(auto_init=False, speaker=speaker)
    c._say_async("Uzun bir açıklama.", wait=True)
    assert _wait_until(lambda: speaker.said)
    c._say_async("Bu okunmamalı.", wait=True)
    c.stop_speech()
    assert speaker.stopped == 1
    time.sleep(0.3)
    assert speaker.said == ["Uzun bir açıklama."]
    assert c._speech_idle()
