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


def test_answers_are_cleaned_before_speaking_and_shown_in_a_box():
    speaker = SlowSpeaker()
    c = ac.AirCanvas(auto_init=False, speaker=speaker)
    c._say_async("<think>plan</think>**Duvar** 2 mm olsun 👍", wait=True)
    assert _wait_until(lambda: speaker.said)
    assert speaker.said == ["Duvar 2 mm olsun."]

    import numpy as np
    c._show_answer("## Cevap\n" + "Uzun bir açıklama cümlesi. " * 20)
    assert c._answer_text.startswith("Cevap") and "#" not in c.assistant_tip
    img = np.zeros((ac.FRAME_H, ac.FRAME_W, 3), np.uint8)
    c._draw_hud(img, 30.0)
    assert img[ac.HUD_TOP - 40, 40].any()      # kutu HUD'un üstünde çizildi


# ─── Dinleme ─────────────────────────────────────────────────────────────────

from air_canvas.listen import ListenError, SpeechListener, filter_hallucination  # noqa: E402


class FakeListener:
    def __init__(self, result):
        self.result = result

    def listen(self):
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def _listen(c):
    c._listen_and_summarize_async()
    assert _wait_until(lambda: not c.voice_listening)


@pytest.mark.parametrize("error, fragment", [
    (ListenError("Mikrofon açılamadı. Mikrofonun bağlı olduğundan emin olun.", "mic"), "mikrofon"),
    (ListenError("İnternet yok ya da Google ses tanımaya ulaşılamadı.", "offline"), "internet"),
    (ListenError("Konuşma duymadım.", "timeout"), "duymad"),
])
def test_listen_errors_are_explained(error, fragment):
    speaker = SlowSpeaker()
    c = ac.AirCanvas(auto_init=False, speaker=speaker, listener=FakeListener(error))
    _listen(c)
    assert fragment in c.assistant_tip.lower().replace("İ".lower(), "i")
    assert _wait_until(lambda: speaker.said) and speaker.said[0].startswith(error.message[:10])


def test_listening_stops_assistant_speech_and_queues_command():
    speaker = SlowSpeaker(duration=1.0)
    c = ac.AirCanvas(auto_init=False, speaker=speaker, listener=FakeListener("kırmızı kare çiz"))
    speaker.say("uzun konuşma")
    _listen(c)
    assert speaker.stopped == 1
    c._drain_actions()
    assert c.color_index == 2 and c.canvas.any()


def test_ai_busy_is_announced_not_silently_dropped():
    c = ac.AirCanvas(auto_init=False)
    c.voice_feedback = False
    c.ai_busy = True
    c._ask_async("duvar kaç milimetre")
    assert "bekleyin" in c.assistant_tip


def test_review_command_works_without_design():
    c = ac.AirCanvas(auto_init=False)
    assert c._parse_voice_command("çizimimi kontrol et")[0] == [("design_review", None)]


def test_transcribe_falls_back_to_whisper_when_offline(monkeypatch):
    from air_canvas import listen as ls
    listener = SpeechListener(engine="auto")

    def no_internet(audio, recognizer=None):
        raise ls._EngineUnavailable("İnternet yok.")

    def no_whisper(audio):
        raise ls._EngineUnavailable("Whisper kurulu değil.")

    monkeypatch.setattr(listener, "_google", no_internet)
    monkeypatch.setattr(listener, "_whisper_text", lambda audio: "dron tasarla")
    assert listener.transcribe(object()) == "dron tasarla"

    monkeypatch.setattr(listener, "_whisper_text", no_whisper)
    with pytest.raises(ListenError) as info:
        listener.transcribe(object())
    assert "İnternet yok." in info.value.message and "Whisper" in info.value.message


def test_whisper_only_engine_never_calls_google(monkeypatch):
    listener = SpeechListener(engine="whisper")
    monkeypatch.setattr(listener, "_google", lambda *a, **k: pytest.fail("Google çağrılmamalı"))
    monkeypatch.setattr(listener, "_whisper_text", lambda audio: "")
    with pytest.raises(ListenError) as info:
        listener.transcribe(object())
    assert info.value.kind == "unclear"


def test_whisper_hallucinations_are_dropped():
    assert filter_hallucination("Altyazı M.K.") == ""
    assert filter_hallucination(" sonraki adım ") == "sonraki adım"
