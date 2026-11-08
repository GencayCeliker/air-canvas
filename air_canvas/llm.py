"""Uzun sesli notları özetlemek için isteğe bağlı yerel LLM istemcisi.

OpenAI uyumlu yerel sunucuları dener (ek paket gerekmez):
    LM Studio  http://127.0.0.1:1234/v1
    Ollama     http://127.0.0.1:11434/v1

AIR_CANVAS_LLM_URL    Belirli bir sunucu adresi (ör. http://127.0.0.1:1234/v1)
AIR_CANVAS_LLM_MODEL  Kullanılacak model adı (boşsa sunucudaki ilk model)

Hiçbiri çalışmıyorsa Air Canvas notu kısaltarak saklar; LLM zorunlu değildir.
"""

import json
import logging
import os
import urllib.request
from typing import List, Optional

log = logging.getLogger(__name__)

DEFAULT_URLS = ["http://127.0.0.1:1234/v1", "http://127.0.0.1:11434/v1"]


class LocalLLMClient:
    def __init__(self, base_urls: Optional[List[str]] = None, model: Optional[str] = None, timeout: float = 20.0):
        env_url = os.getenv("AIR_CANVAS_LLM_URL")
        self.base_urls = base_urls or ([env_url] if env_url else list(DEFAULT_URLS))
        self.model = model or os.getenv("AIR_CANVAS_LLM_MODEL") or ""
        self.timeout = timeout
        self.base_url: Optional[str] = None
        self.available = False
        self._probe()

    def _request(self, url: str, payload: Optional[dict] = None, timeout: float = 2.0) -> dict:
        data = json.dumps(payload).encode("utf-8") if payload is not None else None
        req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _probe(self) -> bool:
        """Çalışan ilk sunucuyu bulur ve model seçer."""
        self.available = False
        for base in self.base_urls:
            try:
                models = self._request(base.rstrip("/") + "/models", timeout=1.5).get("data", [])
            except Exception:
                continue
            ids = [m.get("id") for m in models if m.get("id")]
            if not ids and not self.model:
                continue
            self.base_url = base.rstrip("/")
            if ids and self.model not in ids:
                self.model = ids[0]
            self.available = True
            log.info("[AirCanvas] Yerel LLM bulundu: %s (%s)", self.base_url, self.model)
            return True
        return False

    def generate(self, prompt: str, system: str = "", temperature: float = 0.3, max_tokens: int = 80) -> str:
        if not self.available or not self.base_url:
            return ""
        messages = ([{"role": "system", "content": system}] if system else []) + [
            {"role": "user", "content": prompt}
        ]
        payload = {"model": self.model, "messages": messages, "temperature": temperature,
                   "max_tokens": max_tokens, "stream": False}
        try:
            data = self._request(self.base_url + "/chat/completions", payload, timeout=self.timeout)
            return (data["choices"][0]["message"]["content"] or "").strip()
        except Exception as exc:
            log.debug("[AirCanvas] LLM isteği başarısız: %s", exc)
            self.available = False
            return ""
