"""Yerel model ile çizim (#13): çevrimdışı mod, ölçü sorusu, taslak çizimi — ağ/kamera gerektirmez."""

import json
import time

import numpy as np
import pytest

pytest.importorskip("cv2")
pytest.importorskip("manifold3d")

from air_canvas import canvas as ac  # noqa: E402
from air_canvas.ai import LOCAL_DESIGN_EXAMPLE, DesignAssistant  # noqa: E402


class ScriptedLocal:
    """Sırayla cevap veren sahte yerel model; her isteğin mesajlarını saklar."""

    available = True

    def __init__(self, *replies):
        self.replies = list(replies)
        self.calls = []

    def model_for(self, task):
        return "qwen2.5-coder-7b"

    def chat(self, messages, model=None, **kwargs):
        self.calls.append(messages)
        return self.replies.pop(0) if self.replies else ""


def _design_reply(name="Yerel dron"):
    data = json.loads(json.dumps(LOCAL_DESIGN_EXAMPLE))
    data["name"] = name
    return json.dumps(data, ensure_ascii=False)


def _canvas(local, **kwargs):
    c = ac.AirCanvas(auto_init=False, assistant=DesignAssistant(local_llm=lambda: local, mode="local"), **kwargs)
    c.voice_feedback = False
    return c


def _settle(c, timeout=10):
    deadline = time.time() + timeout
    while time.time() < deadline:
        time.sleep(0.02)
        if not c.design_busy:
            time.sleep(0.05)
            c._drain_actions()
            if not c.design_busy:
                return


def test_local_model_asks_for_dimensions_then_designs_with_the_answer():
    local = ScriptedLocal(json.dumps({"question": "Kaç inç pervane kullanacaksın, 3 mü 5 mi?"}), _design_reply())
    c = _canvas(local)
    c._perform("design_request", "dron tasarla")
    _settle(c)
    assert c.design is None and c._clarify_request == "dron tasarla"
    assert "pervane" in c.assistant_tip

    actions, _ = c._parse_voice_command("5 inç olsun")
    assert actions == [("design_answer", "5 inç olsun")]
    c._perform(*actions[0])
    _settle(c)
    assert c.design is not None and c.design.name == "Yerel dron"
    assert "5 inç olsun" in local.calls[-1][-1]["content"]     # cevap tasarım isteğine eklendi
    assert c._clarify_request == ""


def test_let_the_ai_decide_and_cancel():
    local = ScriptedLocal(json.dumps({"question": "Kaç milimetre olsun?"}), _design_reply())
    c = _canvas(local)
    c._perform("design_request", "kalemlik yap")
    _settle(c)
    c._perform(*c._parse_voice_command("fark etmez sen karar ver")[0][0])
    _settle(c)
    assert "standart" in local.calls[-1][-1]["content"]

    local.replies = [json.dumps({"question": "Kaç milimetre olsun?"})]
    c._perform("design_request", "kutu tasarla")
    _settle(c)
    assert c._parse_voice_command("vazgeç")[0] == [("design_cancel", None)]
    c._perform("design_cancel")
    assert c._clarify_request == ""
    assert c._parse_voice_command("bugün hava güzel")[0][0][0] != "design_answer"   # artık cevap beklenmiyor


def test_dimensions_in_request_skip_the_question():
    local = ScriptedLocal(_design_reply("Kutu"))
    c = _canvas(local)
    c._perform("design_request", "80 milimetrelik kutu tasarla")
    _settle(c)
    assert c.design is not None and len(local.calls) == 1      # soru isteği yok


def test_design_sketch_is_inked_onto_canvas_and_undoable():
    c = _canvas(ScriptedLocal())
    c.assistant = None
    c._perform("design_ink")
    assert not c.canvas.any() and "tasarim" in c.assistant_tip.lower()

    c._assistant = DesignAssistant(use_claude=False)
    c._perform("design_request", "dron tasarla")
    _settle(c)
    assert c._parse_voice_command("taslağı çiz")[0] == [("design_ink", None)]
    c._handle_key(ord("t"))
    x1, y1, x2, y2 = ac.GUIDE_RECT
    inked = c.canvas[y1:y2, x1:x2].any(axis=2)
    assert inked.sum() > 2000 and not c.canvas[:y1].any()
    assert (c.canvas[y1:y2, x1:x2][inked] == c.colors[c.color_index]).all()
    c._undo()
    assert not c.canvas.any()


def test_offline_mode_uses_only_local_engines(monkeypatch):
    c = ac.AirCanvas(auto_init=False, offline=True)
    assert c.ai_mode == "local"
    assert c._get_listener().engine == "whisper"
    assert c._get_assistant().mode == "local" and c._get_assistant()._claude() is None
    from air_canvas import speech
    monkeypatch.setattr(speech.TurkishSpeaker, "_synthesize",
                        lambda self, text: pytest.fail("Çevrimdışı modda metin internete gönderilmemeli"))
    spoken = []
    monkeypatch.setattr(speech.TurkishSpeaker, "_speak_sapi_turkish", lambda self, text: spoken.append(text))
    speaker = c._get_speaker()
    assert speaker.online is False
    speaker.say("Merhaba")
    deadline = time.time() + 2
    while speaker.is_busy() and time.time() < deadline:
        time.sleep(0.01)
    assert spoken == ["Merhaba"]

    img = np.zeros((ac.FRAME_H, ac.FRAME_W, 3), np.uint8)
    c._draw_hud(img, 30.0)            # durum çipinde CEVRIMDISI etiketi çizilir, hata vermemeli


def test_offline_flag_overrides_claude_mode_and_env(monkeypatch):
    monkeypatch.setenv("AIR_CANVAS_OFFLINE", "1")
    assert ac.AirCanvas(auto_init=False).offline is True
    assert ac.AirCanvas(auto_init=False, ai_mode="claude").ai_mode == "local"
    assert ac.AirCanvas(auto_init=False, ai_mode="template").ai_mode == "template"
    monkeypatch.delenv("AIR_CANVAS_OFFLINE")
    assert ac.AirCanvas(auto_init=False).ai_mode == "auto"


def test_cli_offline_flag(monkeypatch):
    from air_canvas import __main__ as cli
    seen = {}

    class FakeCanvas:
        def __init__(self, **kwargs):
            seen.update(kwargs)
            self.available = False

        def _unavailable_message(self):
            return "yok"

    monkeypatch.setattr(cli, "AirCanvas", FakeCanvas)
    assert cli.main(["--offline", "--ai", "local"]) == 1
    assert seen["offline"] is True and seen["ai_mode"] == "local"
