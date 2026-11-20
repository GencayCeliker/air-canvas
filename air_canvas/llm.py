"""Yerel LLM istemcisi: sesli not özetleri, 3D tasarım, sorular ve çizim kontrolü.

OpenAI uyumlu yerel sunucuları dener (ek paket gerekmez):
    LM Studio  http://127.0.0.1:1234/v1
    Ollama     http://127.0.0.1:11434/v1

AIR_CANVAS_LLM_URL           Belirli bir sunucu adresi (ör. http://127.0.0.1:1234/v1)
AIR_CANVAS_LLM_MODEL         Her iş için kullanılacak model (boşsa işe göre otomatik seçilir)
AIR_CANVAS_LLM_DESIGN_MODEL  3D tasarım (JSON) için model — varsayılan: adında "coder" geçen model
AIR_CANVAS_LLM_VISION_MODEL  Çizim kontrolü (görsel) için model — varsayılan: adında "vl"/"vision" geçen model

Hiçbiri çalışmıyorsa Air Canvas yerleşik şablonlarla devam eder; LLM zorunlu değildir.
"""

import base64
import json
import logging
import os
import socket
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional

from .textutil import strip_reasoning

log = logging.getLogger(__name__)

DEFAULT_URLS = ["http://127.0.0.1:1234/v1", "http://127.0.0.1:11434/v1"]

# Model adı ipuçları (küçük harf). Sıra öncelik sırasıdır.
_DESIGN_HINTS = ("coder", "code", "instruct", "qwen", "llama", "mistral", "gemma")
_VISION_HINTS = ("-vl", "vl-", "_vl", "vision", "llava", "minicpm-v", "pixtral", "gemma-3", "gemma-4", "gemma3", "gemma4")
_SKIP_HINTS = ("embed", "embedding", "rerank", "whisper", "tts")


def _pick(ids: List[str], hints) -> Optional[str]:
    for hint in hints:
        for model_id in ids:
            if hint in model_id.lower():
                return model_id
    return None


class LocalLLMClient:
    def __init__(self, base_urls: Optional[List[str]] = None, model: Optional[str] = None, timeout: float = 20.0):
        env_url = os.getenv("AIR_CANVAS_LLM_URL")
        self.base_urls = base_urls or ([env_url] if env_url else list(DEFAULT_URLS))
        self.model = model or os.getenv("AIR_CANVAS_LLM_MODEL") or ""
        self._fixed_model = bool(self.model)
        self.timeout = timeout
        self.base_url: Optional[str] = None
        self.models: List[str] = []
        self.available = False
        self._probe()

    def _request(self, url: str, payload: Optional[dict] = None, timeout: float = 2.0) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _probe(self) -> bool:
        """Çalışan ilk sunucuyu bulur ve model listesini alır."""
        self.available = False
        for base in self.base_urls:
            try:
                models = self._request(base.rstrip("/") + "/models", timeout=1.5).get("data", [])
            except Exception:
                continue
            ids = [m.get("id") for m in models if m.get("id")]
            ids = [i for i in ids if not any(s in i.lower() for s in _SKIP_HINTS)]
            if not ids and not self.model:
                continue
            self.base_url = base.rstrip("/")
            self.models = ids
            if not self._fixed_model or self.model not in ids:
                self.model = self.model if self._fixed_model else (ids[0] if ids else self.model)
            self.available = True
            log.info("[AirCanvas] Yerel LLM bulundu: %s (%s)", self.base_url, ", ".join(ids) or self.model)
            return True
        return False

    def model_for(self, task: str) -> str:
        """İşe uygun modeli seçer: ``design`` (JSON), ``vision`` (görsel) veya ``chat``."""
        env = {"design": "AIR_CANVAS_LLM_DESIGN_MODEL", "vision": "AIR_CANVAS_LLM_VISION_MODEL"}.get(task)
        if env and os.getenv(env):
            return os.getenv(env, "")
        if self._fixed_model:
            return self.model
        if task == "design":
            return _pick(self.models, _DESIGN_HINTS) or self.model
        if task == "vision":
            return _pick(self.models, _VISION_HINTS) or ""
        return self.model

    def chat(self, messages: List[Dict[str, Any]], *, model: Optional[str] = None, temperature: float = 0.3,
             max_tokens: int = 400, timeout: Optional[float] = None,
             json_schema: Optional[Dict[str, Any]] = None) -> str:
        """Sohbet isteği gönderir; yanıt metnini (yoksa "") döndürür.

        json_schema verilirse sunucudan şemaya uyan JSON istenir (LM Studio / Ollama destekler);
        sunucu bunu reddederse şemasız tekrar denenir.
        """
        if not self.available or not self.base_url:
            return ""
        payload: Dict[str, Any] = {"model": model or self.model, "messages": messages,
                                   "temperature": temperature, "max_tokens": max_tokens, "stream": False}
        if json_schema is not None:
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "result", "strict": True, "schema": json_schema}}
        try:
            data = self._request(self.base_url + "/chat/completions", payload, timeout=timeout or self.timeout)
        except urllib.error.HTTPError as exc:
            if json_schema is not None and exc.code in (400, 422):
                log.info("[AirCanvas] Sunucu JSON şemasını desteklemiyor, şemasız deneniyor.")
                return self.chat(messages, model=model, temperature=temperature, max_tokens=max_tokens,
                                 timeout=timeout)
            log.warning("[AirCanvas] LLM isteği reddedildi (%s): %s", exc.code, exc.read()[:300])
            return ""
        except (socket.timeout, TimeoutError) as exc:
            log.warning("[AirCanvas] LLM yanıtı zaman aşımına uğradı: %s", exc)
            return ""   # Sunucu çalışıyor, yalnızca yavaş: kullanılamaz diye işaretleme.
        except Exception as exc:
            if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
                log.warning("[AirCanvas] LLM yanıtı zaman aşımına uğradı: %s", exc)
                return ""
            log.debug("[AirCanvas] LLM isteği başarısız: %s", exc)
            self.available = False
            return ""
        try:
            content = data["choices"][0]["message"].get("content") or ""
        except (KeyError, IndexError, TypeError, AttributeError):
            return ""
        # Düşünce blokları (<think>) asla seslendirilmez; ayrı "reasoning_content" alanı zaten yok sayılır.
        return strip_reasoning(content)

    def generate(self, prompt: str, system: str = "", temperature: float = 0.3, max_tokens: int = 80,
                 **kwargs) -> str:
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        return self.chat(messages, temperature=temperature, max_tokens=max_tokens, **kwargs)

    @staticmethod
    def image_message(text: str, png_bytes: bytes) -> Dict[str, Any]:
        """OpenAI biçiminde metin + PNG içeren kullanıcı mesajı."""
        b64 = base64.standard_b64encode(png_bytes).decode("ascii")
        return {"role": "user", "content": [
            {"type": "text", "text": text},
            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
        ]}
