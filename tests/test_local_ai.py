"""Yerel model (LM Studio / Ollama) ile tasarım, soru ve çizim kontrolü — ağ gerektirmez."""

import json

import pytest

pytest.importorskip("manifold3d")

from air_canvas import design as dsg  # noqa: E402
from air_canvas.ai import LOCAL_DESIGN_EXAMPLE, DesignAssistant, _extract_json  # noqa: E402


class FakeLocalLLM:
    """LocalLLMClient gibi davranan sahte istemci: chat(), model_for(), image_message()."""

    available = True

    def __init__(self, replies, models=("qwen2.5-coder-7b", "qwen2.5-vl-7b")):
        self.replies = list(replies)
        self.calls = []
        self.models = list(models)

    def model_for(self, task):
        if task == "design":
            return next((m for m in self.models if "coder" in m), self.models[0])
        if task == "vision":
            return next((m for m in self.models if "vl" in m), "")
        return self.models[0]

    def chat(self, messages, model=None, **kwargs):
        self.calls.append({"messages": messages, "model": model, **kwargs})
        reply = self.replies.pop(0) if self.replies else ""
        if isinstance(reply, Exception):
            raise reply
        return reply

    @staticmethod
    def image_message(text, png):
        return {"role": "user", "content": [{"type": "text", "text": text},
                                            {"type": "image_url", "image_url": {"url": "data:,"}}]}


class ExplodingClaude:
    def __init__(self):
        self.beta = self

    @property
    def messages(self):
        pytest.fail("Yerel modda Claude çağrılmamalı")


def _drone_like(name="Yerel dron"):
    data = json.loads(json.dumps(LOCAL_DESIGN_EXAMPLE))
    data["name"] = name
    data["parts"][0]["shapes"].append({"kind": "cylinder", "op": "subtract", "size": [3.2, 3.2, 10],
                                       "pos": [20, 0, 2], "rot": [0, 0, 0], "segments": 0})
    return json.dumps(data, ensure_ascii=False)


def _example_copy(name):
    data = json.loads(json.dumps(LOCAL_DESIGN_EXAMPLE))
    data["name"] = name
    return json.dumps(data, ensure_ascii=False)


def test_template_is_preferred_over_local_model():
    local = FakeLocalLLM([_drone_like()])
    design, source = DesignAssistant(local_llm=lambda: local, use_claude=False).design("dron tasarla")
    assert source == "sablon" and design.name == "Mini dron gövdesi" and local.calls == []


def test_auto_mode_uses_local_model_for_objects_without_a_template():
    local = FakeLocalLLM([_drone_like("Yerel uçak")])
    assistant = DesignAssistant(local_llm=lambda: local, use_claude=False)
    design, source = assistant.design("uçak tasarla")
    assert source == "yerel LLM" and design.name == "Yerel uçak"
    call = local.calls[0]
    assert call["model"] == "qwen2.5-coder-7b"              # işe göre model
    assert call["json_schema"] is dsg.DESIGN_SCHEMA and call["timeout"] >= 120


def test_local_mode_never_calls_claude():
    local = FakeLocalLLM([_drone_like()])
    assistant = DesignAssistant(local_llm=lambda: local, client=ExplodingClaude(), mode="local")
    assert assistant.design("uçak tasarla")[1] == "yerel LLM"
    assert assistant.ask("duvar kaç mm") == ""             # yanıt listesi bitti → boş, Claude yok


def test_invalid_local_json_is_retried_with_error_feedback():
    local = FakeLocalLLM(["<think>plan</think>bu bir JSON değil", _drone_like()])
    design, source = DesignAssistant(local_llm=lambda: local, mode="local").design("kalemlik yap")
    assert source == "yerel LLM" and len(local.calls) == 2
    assert "kullanilamadi" in local.calls[1]["messages"][-1]["content"]


def test_local_failure_without_template_returns_nothing():
    local = FakeLocalLLM(["{}", "{}"])
    assert DesignAssistant(local_llm=lambda: local, mode="local").design("uçak tasarla") == (None, "")
    assert len(local.calls) == 2


def test_copy_of_the_example_is_retried_then_rejected():
    # Küçük model "Uçak" adıyla örnekteki kalemliği döndürdü (gerçek LM Studio günlüğündeki "Vida" gibi).
    local = FakeLocalLLM([_example_copy("Uçak"), _example_copy("Uçak")])
    assert DesignAssistant(local_llm=lambda: local, mode="local").design("uçak tasarla")[0] is None
    assert len(local.calls) == 2 and "kopyasi" in local.calls[1]["messages"][-1]["content"]


def test_copy_is_accepted_when_a_simple_container_was_asked():
    local = FakeLocalLLM([_example_copy("Bardak")])
    assert DesignAssistant(local_llm=lambda: local, mode="local").design("bardak yap")[1] == "yerel LLM"


def test_local_timeout_does_not_retry():
    local = FakeLocalLLM([""])
    assert DesignAssistant(local_llm=lambda: local, mode="local").design("uzay istasyonu")[0] is None
    assert len(local.calls) == 1


def test_template_mode_ignores_local_model():
    local = FakeLocalLLM([_drone_like()])
    assert DesignAssistant(local_llm=lambda: local, mode="template").design("dron")[1] == "sablon"
    assert local.calls == []


def test_review_uses_local_vision_model():
    local = FakeLocalLLM(["Kolları biraz daha uzun çiz."])
    answer = DesignAssistant(local_llm=lambda: local, mode="local").review(b"png")
    assert answer == "Kolları biraz daha uzun çiz."
    assert local.calls[0]["model"] == "qwen2.5-vl-7b"
    assert local.calls[0]["messages"][-1]["content"][1]["type"] == "image_url"


def test_review_without_vision_model_returns_empty():
    local = FakeLocalLLM(["x"], models=("qwen2.5-coder-7b",))
    assert DesignAssistant(local_llm=lambda: local, mode="local").review(b"png") == ""
    assert local.calls == []


def test_extract_json_ignores_reasoning_and_fences():
    raw = "<think>{\"name\": \"yanlis\"}</think>\n```json\n{\"name\": \"dogru\"}\n```"
    assert _extract_json(raw) == {"name": "dogru"}


def test_normalize_accepts_common_local_model_variants():
    data = {"design": {"name": "Kutu", "parts": [{
        "name": "Kutu",
        "shapes": [
            {"type": "cube", "operation": "add", "size": [20, 20, 10], "position": [0, 0, 5]},
            {"type": "cylinder", "operation": "hole", "size": [5, 5, 20], "position": [0, 0, 5]},
        ]}]}}
    norm = dsg.normalize_design(data)
    assert norm["name"] == "Kutu"
    shapes = norm["parts"][0]["shapes"]
    assert [s["kind"] for s in shapes] == ["box", "cylinder"]
    assert [s["op"] for s in shapes] == ["add", "subtract"]
    assert shapes[0]["pos"] == [0.0, 0.0, 5.0]
    assert dsg.Design(data).meshes[0][1].size
