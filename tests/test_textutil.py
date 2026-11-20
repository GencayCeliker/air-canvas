"""Model çıktısı temizleme ve yerel LLM yanıt işleme testleri."""

import pytest

from air_canvas.llm import LocalLLMClient
from air_canvas.textutil import clean_for_speech, clean_markdown, strip_reasoning


@pytest.mark.parametrize("raw, expected", [
    ("<think>kullanıcı duvarı soruyor</think>\nDuvar 2 mm olsun.", "Duvar 2 mm olsun."),
    ("düşünce şablonda açıldı...</think>Duvar 2 mm olsun.", "Duvar 2 mm olsun."),
    ("Kısa cevap. <think>token sınırında kesilen düşün", "Kısa cevap."),
    ("<THINKING>a</THINKING>b", "b"),
    ("<think>yalnızca düşünce", ""),
    ("Düz cevap.", "Düz cevap."),
])
def test_strip_reasoning(raw, expected):
    assert strip_reasoning(raw) == expected


def test_clean_for_speech_removes_markdown_and_emoji():
    raw = "## Öneri\n- **Duvar** kalınlığı `2 mm` 👍\n- [PETG](https://x) kullan\n1. Sarkma yok"
    assert clean_markdown(raw) == "Öneri\nDuvar kalınlığı 2 mm\nPETG kullan\nSarkma yok"
    assert clean_for_speech(raw) == "Öneri. Duvar kalınlığı 2 mm. PETG kullan. Sarkma yok."


def test_local_llm_chat_never_returns_reasoning(monkeypatch):
    client = LocalLLMClient.__new__(LocalLLMClient)
    client.available, client.base_url, client.model, client.timeout = True, "http://x/v1", "qwen3", 5
    reply = {"choices": [{"message": {"content": "<think>uzun düşünce</think>Cevap burada.",
                                      "reasoning_content": "gizli"}}]}
    monkeypatch.setattr(client, "_request", lambda *a, **k: reply)
    assert client.generate("soru") == "Cevap burada."
