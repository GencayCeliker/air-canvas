"""Yerel LLM istemcisi: sesli not özetleri, 3D tasarım, sorular ve çizim kontrolü.

OpenAI uyumlu yerel sunucuları dener (ek paket gerekmez):
    LM Studio  http://127.0.0.1:1234/v1
    Ollama     http://127.0.0.1:11434/v1

AIR_CANVAS_LLM_URL           Belirli bir sunucu adresi (ör. http://127.0.0.1:1234/v1)
AIR_CANVAS_LLM_MODEL         Her iş için kullanılacak model (boşsa işe göre otomatik seçilir)
AIR_CANVAS_LLM_DESIGN_MODEL  3D tasarım (JSON) için model — varsayılan: adında "coder" geçen model
AIR_CANVAS_LLM_VISION_MODEL  Çizim kontrolü (görsel) için model — varsayılan: adında "vl"/"vision" geçen model

Model seçimi, sunucuda o an YÜKLÜ olan modelleri tercih eder (LM Studio ``/api/v0/models``,
Ollama ``/api/ps``). Sohbet işleri ayrı bir model yüklemez: iki 7B model aynı anda yüklenince
8 GB'lık bir GPU'nun belleği taşar ve üretim saniyede birkaç token'a düşer.

Yanıtlar akış (stream) olarak alınır: zaman aşımı toplam süre değil, sunucunun hiç veri
göndermediği süredir; yavaş ama çalışan bir model yarıda kesilmez ve ilerleme bildirilebilir.

Hiçbiri çalışmıyorsa Air Canvas yerleşik şablonlarla devam eder; LLM zorunlu değildir.
"""

import base64
import json
import logging
import os
import socket
import time
import urllib.error
import urllib.request
from typing import Any, Callable, Dict, List, Optional

from .textutil import strip_reasoning

log = logging.getLogger(__name__)

DEFAULT_URLS = ["http://127.0.0.1:1234/v1", "http://127.0.0.1:11434/v1"]

# Model adı ipuçları (küçük harf). Sıra öncelik sırasıdır.
_CODER_HINTS = ("coder", "code")
_DESIGN_HINTS = ("coder", "code", "instruct", "qwen", "llama", "mistral", "gemma")
_VISION_HINTS = ("-vl", "vl-", "_vl", "vision", "llava", "minicpm-v", "pixtral", "gemma-3", "gemma-4", "gemma3", "gemma4")
_SKIP_HINTS = ("embed", "embedding", "rerank", "whisper", "tts")
_STATE_TTL = 10.0   # Yüklü model listesi en fazla bu sıklıkla (saniye) yeniden sorulur


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
        self.loaded: List[str] = []          # Sunucuda şu an belleğe yüklü modeller
        self.types: Dict[str, str] = {}      # LM Studio model türleri: llm / vlm
        self._state_at = 0.0
        self._warned_multi = False
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
            self._refresh_state(force=True)
            return True
        return False

    # ─── Yüklü modeller ───────────────────────────────────────────────────────

    def _refresh_state(self, force: bool = False) -> None:
        """Sunucuda hangi modellerin belleğe yüklü olduğunu öğrenir (LM Studio veya Ollama)."""
        if not self.base_url or (not force and time.time() - self._state_at < _STATE_TTL):
            return
        self._state_at = time.time()
        root = self.base_url[:-3] if self.base_url.endswith("/v1") else self.base_url
        loaded: List[str] = []
        try:   # LM Studio
            data = self._request(root + "/api/v0/models", timeout=1.0).get("data", [])
            self.types = {m.get("id"): str(m.get("type") or "") for m in data if m.get("id")}
            loaded = [m.get("id") for m in data if m.get("state") == "loaded"]
        except Exception:
            try:   # Ollama
                data = self._request(root + "/api/ps", timeout=1.0).get("models", [])
                loaded = [m.get("model") or m.get("name") for m in data]
            except Exception:
                return
        self.loaded = [m for m in self.models if m in loaded]
        if len(self.loaded) > 1 and not self._warned_multi:
            self._warned_multi = True
            log.warning("[AirCanvas] Yerel sunucuda %d model aynı anda yüklü (%s). GPU belleği taşarsa yanıtlar "
                        "çok yavaşlar; kullanmadıklarınızı LM Studio/Ollama'dan kaldırın.",
                        len(self.loaded), ", ".join(self.loaded))

    def _is_vision(self, model_id: str) -> bool:
        return self.types.get(model_id) == "vlm" or bool(_pick([model_id], _VISION_HINTS))

    def model_for(self, task: str) -> str:
        """İşe uygun modeli seçer: ``design`` (JSON), ``vision`` (görsel) veya ``chat``.

        Yüklü modeller önce gelir; sohbet, ikinci bir modeli belleğe almamak için yüklü
        bir metin modelini (yoksa tasarım modelini) kullanır."""
        env = {"design": "AIR_CANVAS_LLM_DESIGN_MODEL", "vision": "AIR_CANVAS_LLM_VISION_MODEL"}.get(task)
        if env and os.getenv(env):
            return os.getenv(env, "")
        if self._fixed_model:
            return self.model
        self._refresh_state()
        loaded = self.loaded
        text_loaded = [m for m in loaded if not self._is_vision(m)]
        if task == "vision":
            vision_loaded = [m for m in loaded if self._is_vision(m)]
            vision_all = [m for m in self.models if self._is_vision(m)]
            return (_pick(vision_loaded, _VISION_HINTS) or (vision_loaded[0] if vision_loaded else None)
                    or _pick(vision_all, _VISION_HINTS) or (vision_all[0] if vision_all else ""))
        text_all = [m for m in self.models if not self._is_vision(m)]
        design = (_pick(loaded, _CODER_HINTS) or _pick(self.models, _CODER_HINTS)
                  or _pick(text_loaded, _DESIGN_HINTS) or (text_loaded[0] if text_loaded else None)
                  or _pick(text_all, _DESIGN_HINTS) or (text_all[0] if text_all else None) or self.model)
        if task == "design":
            return design
        return (_pick(text_loaded, _DESIGN_HINTS) or (text_loaded[0] if text_loaded else None)
                or (loaded[0] if loaded else None) or design)

    # ─── İstekler ─────────────────────────────────────────────────────────────

    def _stream(self, payload: Dict[str, Any], timeout: float,
                on_progress: Optional[Callable[[int], None]] = None) -> str:
        """Yanıtı akış olarak okur; ``timeout`` iki veri parçası arasında beklenen en uzun süredir."""
        payload = dict(payload, stream=True)
        req = urllib.request.Request(self.base_url + "/chat/completions", data=json.dumps(payload).encode("utf-8"),
                                     headers={"Content-Type": "application/json"})
        parts: List[str] = []
        tokens = 0
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            if "event-stream" not in (resp.headers.get("Content-Type") or ""):
                body = json.loads(resp.read().decode("utf-8"))   # Akışı desteklemeyen sunucu
                return body["choices"][0]["message"].get("content") or ""
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                chunk = line[5:].strip()
                if chunk == "[DONE]":
                    break
                try:
                    data = json.loads(chunk)
                except ValueError:
                    continue
                if data.get("error"):
                    raise RuntimeError(str(data["error"])[:300])
                for choice in data.get("choices") or []:
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    if text or delta.get("reasoning_content"):
                        tokens += 1
                    if text:
                        parts.append(text)
                if on_progress is not None and tokens:
                    try:
                        on_progress(tokens)
                    except Exception:
                        pass
        return "".join(parts)

    def chat(self, messages: List[Dict[str, Any]], *, model: Optional[str] = None, temperature: float = 0.3,
             max_tokens: int = 400, timeout: Optional[float] = None,
             json_schema: Optional[Dict[str, Any]] = None,
             on_progress: Optional[Callable[[int], None]] = None) -> str:
        """Sohbet isteği gönderir; yanıt metnini (yoksa "") döndürür.

        json_schema verilirse sunucudan şemaya uyan JSON istenir (LM Studio / Ollama destekler);
        sunucu bunu reddederse şemasız tekrar denenir. ``on_progress(token_sayısı)`` akış sırasında
        çağrılır. ``model`` verilmezse sohbet için uygun (yüklü) model seçilir.
        """
        if not self.available or not self.base_url:
            return ""
        payload: Dict[str, Any] = {"model": model or self.model_for("chat"), "messages": messages,
                                   "temperature": temperature, "max_tokens": max_tokens}
        if json_schema is not None:
            payload["response_format"] = {"type": "json_schema",
                                          "json_schema": {"name": "result", "strict": True, "schema": json_schema}}
        try:
            content = self._stream(payload, timeout or self.timeout, on_progress)
        except urllib.error.HTTPError as exc:
            if json_schema is not None and exc.code in (400, 422):
                log.info("[AirCanvas] Sunucu JSON şemasını desteklemiyor, şemasız deneniyor.")
                return self.chat(messages, model=model, temperature=temperature, max_tokens=max_tokens,
                                 timeout=timeout, on_progress=on_progress)
            log.warning("[AirCanvas] LLM isteği reddedildi (%s): %s", exc.code, exc.read()[:300])
            return ""
        except (socket.timeout, TimeoutError) as exc:
            log.warning("[AirCanvas] LLM yanıtı zaman aşımına uğradı: %s", exc)
            return ""   # Sunucu çalışıyor, yalnızca yavaş: kullanılamaz diye işaretleme.
        except (KeyError, IndexError, TypeError, ValueError, AttributeError, RuntimeError) as exc:
            log.warning("[AirCanvas] LLM yanıtı okunamadı: %s", exc)
            return ""
        except Exception as exc:
            if isinstance(getattr(exc, "reason", None), (socket.timeout, TimeoutError)):
                log.warning("[AirCanvas] LLM yanıtı zaman aşımına uğradı: %s", exc)
                return ""
            log.debug("[AirCanvas] LLM isteği başarısız: %s", exc)
            self.available = False
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
