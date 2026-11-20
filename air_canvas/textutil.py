"""Model çıktılarını seslendirme ve ekran için temizleyen yardımcılar."""

import re

_REASON_TAGS = r"(?:think|thinking|reasoning|reflection)"
_REASON_BLOCK = re.compile(rf"<({_REASON_TAGS})>.*?</\1>", re.S | re.I)
_REASON_OPEN = re.compile(rf"<{_REASON_TAGS}>", re.I)
_REASON_CLOSE = re.compile(rf"</{_REASON_TAGS}>", re.I)

_EMOJI = re.compile("[\U0001F000-\U0001FAFF☀-➿⬀-⯿️‍]")


def strip_reasoning(text: str) -> str:
    """Akıl yürüten modellerin (Qwen3, DeepSeek-R1 …) düşünce bloklarını atar.

    - ``<think>…</think>`` tamamen silinir.
    - Yalnızca kapanış etiketi varsa (açılış sohbet şablonundadır) etiketten öncesi atılır.
    - Kapanmamış blok (token sınırında kesilmiş) varsa etiketten sonrası atılır.
    """
    text = _REASON_BLOCK.sub("", text or "")
    closes = list(_REASON_CLOSE.finditer(text))
    if closes:
        text = text[closes[-1].end():]
    opened = _REASON_OPEN.search(text)
    if opened:
        text = text[:opened.start()]
    return text.strip()


def clean_markdown(text: str) -> str:
    """Markdown işaretlerini ve emojileri atar; satırları korur."""
    text = strip_reasoning(text)
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    text = re.sub(r"`([^`]*)`", r"\1", text)
    text = re.sub(r"!?\[([^\]]+)\]\([^)]*\)", r"\1", text)           # bağlantılar
    text = re.sub(r"^\s{0,3}(?:#{1,6}|>|[-*+•]|\d+[.)])\s+", "", text, flags=re.M)
    text = re.sub(r"(\*\*|__|\*|~~)(.+?)\1", r"\2", text)               # kalın / italik
    text = re.sub(r"^\s*[-*_|=]{3,}\s*$", "", text, flags=re.M)         # yatay çizgi / tablo
    text = text.replace("|", " ")
    text = _EMOJI.sub("", text)
    lines = [re.sub(r"[ \t]+", " ", ln).strip() for ln in text.splitlines()]
    return "\n".join(ln for ln in lines if ln)


def clean_for_speech(text: str) -> str:
    """Sesli okunacak düz metin: markdown yok, satırlar cümle olarak birleşir."""
    parts = []
    for line in clean_markdown(text).splitlines():
        line = line.strip()
        if line and line[-1] not in ".!?:;,":
            line += "."
        parts.append(line)
    return " ".join(parts)
