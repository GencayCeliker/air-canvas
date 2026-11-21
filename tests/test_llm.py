"""Yerel LLM istemcisi: yüklü modele göre seçim ve akışlı yanıt okuma (sunucu gerektirmez)."""

import io
import json
import urllib.error

from air_canvas import llm as llm_mod
from air_canvas.ai import DesignAssistant
from air_canvas.llm import LocalLLMClient

MODELS = ["qwen/qwen2.5-vl-7b", "stefancosma/qwen2.5-coder-7b-instruct", "google/gemma-4-e4b"]
TYPES = {"qwen/qwen2.5-vl-7b": "vlm", "stefancosma/qwen2.5-coder-7b-instruct": "llm", "google/gemma-4-e4b": "vlm"}


def _client(monkeypatch, loaded, models=MODELS, types=TYPES):
    monkeypatch.setattr(LocalLLMClient, "_probe", lambda self: False)
    monkeypatch.delenv("AIR_CANVAS_LLM_MODEL", raising=False)
    monkeypatch.delenv("AIR_CANVAS_LLM_DESIGN_MODEL", raising=False)
    monkeypatch.delenv("AIR_CANVAS_LLM_VISION_MODEL", raising=False)
    c = LocalLLMClient(base_urls=["http://127.0.0.1:1234/v1"])
    c.available, c.base_url, c.models, c.model = True, "http://127.0.0.1:1234/v1", list(models), models[0]

    def fake_request(url, payload=None, timeout=2.0):
        assert url.endswith("/api/v0/models")
        return {"data": [{"id": m, "type": types.get(m, "llm"),
                          "state": "loaded" if m in loaded else "not-loaded"} for m in models]}

    monkeypatch.setattr(c, "_request", fake_request)
    return c


def test_chat_never_loads_the_vision_model_when_nothing_is_loaded(monkeypatch):
    """Eski hata: sohbet listedeki ilk modele (VL) gidiyor, tasarım coder'ı yüklüyordu → 8 GB GPU taşıyordu."""
    c = _client(monkeypatch, loaded=[])
    assert c.model_for("design") == "stefancosma/qwen2.5-coder-7b-instruct"
    assert c.model_for("chat") == "stefancosma/qwen2.5-coder-7b-instruct"
    assert c.model_for("vision") == "qwen/qwen2.5-vl-7b"


def test_chat_reuses_an_already_loaded_model(monkeypatch):
    c = _client(monkeypatch, loaded=["google/gemma-4-e4b"])
    assert c.model_for("chat") == "google/gemma-4-e4b"          # ikinci model belleğe alınmaz
    assert c.model_for("vision") == "google/gemma-4-e4b"        # yüklü görsel model önce
    assert c.model_for("design") == "stefancosma/qwen2.5-coder-7b-instruct"   # JSON için coder daha iyi


def test_multiple_loaded_models_are_reported(monkeypatch, caplog):
    c = _client(monkeypatch, loaded=["qwen/qwen2.5-vl-7b", "stefancosma/qwen2.5-coder-7b-instruct"])
    with caplog.at_level("WARNING"):
        assert c.model_for("chat") == "stefancosma/qwen2.5-coder-7b-instruct"
    assert "2 model" in caplog.text
    c.model_for("design")
    assert caplog.text.count("aynı anda yüklü") == 1           # yalnızca bir kez uyarır


def test_env_model_overrides_everything(monkeypatch):
    c = _client(monkeypatch, loaded=[])
    monkeypatch.setenv("AIR_CANVAS_LLM_DESIGN_MODEL", "benim-modelim")
    assert c.model_for("design") == "benim-modelim"


class _FakeResponse(io.BytesIO):
    def __init__(self, body: bytes, ctype: str):
        super().__init__(body)
        self.headers = {"Content-Type": ctype}

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def _sse(*deltas):
    lines = [b"data: " + json.dumps({"choices": [{"delta": d}]}).encode() + b"\n\n" for d in deltas]
    return b"".join(lines) + b"data: [DONE]\n\n"


def test_streamed_reply_is_joined_and_progress_reported(monkeypatch):
    c = _client(monkeypatch, loaded=["stefancosma/qwen2.5-coder-7b-instruct"])
    sent = {}

    def fake_urlopen(req, timeout):
        sent["payload"], sent["timeout"] = json.loads(req.data), timeout
        return _FakeResponse(_sse({"reasoning_content": "düşün"}, {"content": "Duvar "}, {"content": "2 mm."}),
                             "text/event-stream")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    progress = []
    assert c.chat([{"role": "user", "content": "?"}], timeout=42, on_progress=progress.append) == "Duvar 2 mm."
    assert sent["payload"]["stream"] is True and sent["timeout"] == 42
    assert sent["payload"]["model"] == "stefancosma/qwen2.5-coder-7b-instruct"
    assert progress == [1, 2, 3]


def test_non_streaming_server_still_works(monkeypatch):
    c = _client(monkeypatch, loaded=[])
    body = json.dumps({"choices": [{"message": {"content": "Tamam."}}]}).encode()
    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", lambda req, timeout: _FakeResponse(body, "application/json"))
    assert c.chat([{"role": "user", "content": "?"}]) == "Tamam."


def test_schema_rejection_retries_without_schema(monkeypatch):
    c = _client(monkeypatch, loaded=[])
    payloads = []

    def fake_urlopen(req, timeout):
        payloads.append(json.loads(req.data))
        if "response_format" in payloads[-1]:
            raise urllib.error.HTTPError(req.full_url, 400, "bad", {}, io.BytesIO(b"no schema"))
        return _FakeResponse(_sse({"content": "{}"}), "text/event-stream")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    assert c.chat([{"role": "user", "content": "?"}], json_schema={"type": "object"}) == "{}"
    assert len(payloads) == 2 and "response_format" not in payloads[1]


def test_timeout_mid_stream_returns_empty_but_keeps_server_available(monkeypatch):
    c = _client(monkeypatch, loaded=[])

    def fake_urlopen(req, timeout):
        raise TimeoutError("timed out")

    monkeypatch.setattr(llm_mod.urllib.request, "urlopen", fake_urlopen)
    assert c.chat([{"role": "user", "content": "?"}]) == ""
    assert c.available


def test_design_progress_is_shown_with_slow_warning(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("air_canvas.ai.time.time", lambda: clock[0])
    a = DesignAssistant(mode="local")
    statuses = []
    a.status_callback = statuses.append
    report = a._progress_reporter("Yerel model tasarliyor")
    clock[0] += 2
    report(40)
    report(41)                                  # aynı saniye içinde tekrar bildirilmez
    clock[0] += 18
    report(100)                                 # 5 token/sn, 20 sn sonra → yavaş uyarısı
    assert statuses[0] == "Yerel model tasarliyor ... 40 token (20/sn)"
    assert len(statuses) == 2 and "yavas" in statuses[1] and "100 token (5/sn)" in statuses[1]


def test_local_design_passes_progress_callback():
    class Fake:
        available = True

        def __init__(self):
            self.kwargs = None

        def model_for(self, task):
            return "coder"

        def chat(self, messages, model=None, **kwargs):
            self.kwargs = kwargs
            kwargs["on_progress"](5)
            return ""

    fake = Fake()
    a = DesignAssistant(local_llm=fake, mode="local")
    assert a._local_design(fake, "kutu") is None
    assert callable(fake.kwargs["on_progress"]) and fake.kwargs["timeout"] == a.local_timeout
