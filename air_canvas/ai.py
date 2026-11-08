"""air_canvas.ai — Tasarım asistanı (Claude API → yerel LLM → hazır şablonlar).

Claude kullanmak için ``pip install "air-canvas[ai]"`` kurun ve kimlik bilgisi verin:
    ANTHROPIC_API_KEY=...            (veya ``ant auth login`` profili)
    AIR_CANVAS_CLAUDE_MODEL=...      İsteğe bağlı model (varsayılan: claude-opus-5-5)

Claude yoksa LM Studio / Ollama denenir (bkz. ``llm.py``); o da yoksa
``design.match_template`` ile yerleşik şablonlar kullanılır.
"""

import base64
import json
import logging
import os
import re
from typing import Any, Callable, Dict, Optional, Tuple

from . import design as dsg

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"

DESIGN_SYSTEM = """Sen Air Canvas'in 3D baski tasarim asistanisin. Kullanici sesli olarak bir nesne istiyor
(vidadan dron govdesine, araba modelinden bilgisayar parcalarina kadar). Gorevin bu nesneyi FDM 3D yaziciyla
basilabilecek parcalara ayirip, her parcayi basit katilarla modellemek ve kullaniciya adim adim anlatmak.

Modelleme kurallari:
- Birim milimetre, Z yukari. Katilar: box (size=[en, boy, yukseklik]), cylinder (size=[alt cap, ust cap, yukseklik];
  ust cap 0 ise koni; segments=6 altigen, 0 puruzsuz), sphere (size=[cap x, cap y, cap z]).
- pos sekil MERKEZIDIR; rot derece cinsinden once X, sonra Y, sonra Z ekseni etrafinda.
- Sekiller listedeki SIRAYLA uygulanir: "add" ekler, "subtract" o ana kadarki katidan oyar. Delik, cep, kanal,
  hafifletme boslugu icin subtract kullan. Bir bosluk actiktan sonra icine sekil eklemek icin onu sonra ekle.
- Her parcanin sekilleri parcanin KENDI koordinatlarinda ve BASKI YONUNDE verilir: taban z=0 duzlemine oturur,
  hicbir sey z<0'da olmaz, en genis duz yuzey asagida.
- placements parcanin montajdaki konumlaridir (pos + rot); her konum bir baski adedidir (4 kol = 4 placement).
  Montaj gercekci olsun: parcalar birbirine dogru yerde dokunsun.
- En fazla 10 parca, parca basina en fazla 50 sekil. Gercekci ve standart olculer kullan (motor delik araliklari,
  M3 = 3.2 mm delik, 2.5" SSD, fan vida araliklari vb.).

Baskiya uygunluk:
- Minimum duvar 1.2 mm, minimum ozellik 0.8 mm. 45 dereceden fazla sarkma ve destek gerektiren koprulerden kacin.
- Gecme/hareketli parcalarda 0.2-0.3 mm bosluk birak. Vida dislerini modelleme; delik birak ve ipucunda belirt.
- Yuk tasiyan parcalar icin malzeme ve doluluk oner (PLA, PETG, TPU, naylon).

Metinler (Turkce, dogal konusma dili; sesli okunacak):
- summary: 1-2 cumle, ne tasarlandigini ve buyuklugunu anlat.
- step: o parcayi cizerken kullaniciya yol gosteren 2-3 cumle; once ana govdeyi, sonra delik ve detaylari, olculeriyle.
- purpose: parcanin gorevi, tek cumle. print_orientation: baski yonu, malzeme, doluluk; kisa.
- print_tips: 2-5 kisa madde (montaj, malzeme, ayarlar).
- Tum metinleri dogru Turkce yazimla (ç, ğ, ı, ö, ş, ü) yaz; sesli okunacaklar."""

HELP_SYSTEM = """Sen Air Canvas'in 3D tasarim ve cizim asistanisin. Kullanici havada parmagiyla ciziyor ve seninle
sesli konusuyor. Cevaplarin sesli okunacak: Turkce, en fazla 3 kisa cumle, madde isareti veya markdown kullanma.
Olcu verirken milimetre kullan. 3D baskiya uygunluk (duvar kalinligi, sarkma, tolerans, malzeme) konusunda pratik ol."""


def _context_text(design: Optional["dsg.Design"], step: int) -> str:
    if design is None:
        return "Su anda acik bir tasarim yok."
    lines = [design.overview_text()]
    if 0 <= step < len(design.parts):
        lines.append("Kullanicinin su anki adimi:\n" + design.step_text(step))
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[Any]:
    """Serbest metindeki ilk JSON nesnesini ayıklar (yerel LLM çıktısı için)."""
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(candidate)
    except Exception:
        return None


class DesignAssistant:
    """Tasarım üretir, soruları yanıtlar, çizimi değerlendirir.

    local_llm: ``generate()`` ve ``available`` olan isteğe bağlı yerel istemci veya onu
               döndüren bir fonksiyon (her çağrıda güncel durumu almak için).
    """

    def __init__(self, local_llm: Optional[Callable[[], Any]] = None, client: Any = None,
                 model: Optional[str] = None, use_claude: bool = True):
        self._local_llm = local_llm
        self.model = model or os.getenv("AIR_CANVAS_CLAUDE_MODEL") or DEFAULT_MODEL
        self._client = client
        self._claude_disabled = not use_claude
        self.last_source = ""

    # ─── Claude ───────────────────────────────────────────────────────────────

    def _claude(self):
        if self._claude_disabled:
            return None
        if self._client is None:
            try:
                import anthropic
                self._client = anthropic.Anthropic(timeout=180.0, max_retries=1)
            except ImportError:
                log.info("[AI] anthropic paketi yok; Claude kullanilmayacak (pip install anthropic).")
                self._claude_disabled = True
                return None
            except Exception as exc:
                log.info("[AI] Claude istemcisi kurulamadi: %s", exc)
                self._claude_disabled = True
                return None
        return self._client

    @property
    def claude_ready(self) -> bool:
        return self._claude() is not None

    def _call_claude(self, *, system: str, content: Any, effort: str, max_tokens: int,
                     output_format: Optional[Dict[str, Any]] = None) -> Optional[str]:
        """Tek bir Claude isteği; metni döndürür, başarısızsa ``None``."""
        client = self._claude()
        if client is None:
            return None
        import anthropic

        output_config: Dict[str, Any] = {"effort": effort}
        if output_format is not None:
            output_config["format"] = output_format
        try:
            response = client.beta.messages.create(
                model=self.model,
                max_tokens=max_tokens,
                betas=[FALLBACK_BETA],
                fallbacks="default",
                system=system,
                output_config=output_config,
                messages=[{"role": "user", "content": content}],
            )
        except (anthropic.AuthenticationError, anthropic.PermissionDeniedError) as exc:
            log.warning("[AI] Claude kimlik dogrulamasi basarisiz, yerel moda geciliyor: %s", exc)
            self._claude_disabled = True
            return None
        except anthropic.NotFoundError as exc:
            log.warning("[AI] Claude modeli bulunamadi (%s): %s", self.model, exc)
            self._claude_disabled = True
            return None
        except anthropic.RateLimitError as exc:
            log.warning("[AI] Claude hiz sinirina takildi: %s", exc)
            return None
        except anthropic.APIStatusError as exc:
            log.warning("[AI] Claude API hatasi (%s): %s", exc.status_code, exc)
            return None
        except anthropic.APIConnectionError as exc:
            log.warning("[AI] Claude'a baglanilamadi: %s", exc)
            return None
        except anthropic.AnthropicError as exc:  # ör. kimlik bilgisi hiç yok
            log.info("[AI] Claude kullanilamiyor: %s", exc)
            self._claude_disabled = True
            return None

        if response.stop_reason == "refusal":
            log.info("[AI] Claude istegi reddetti: %s", getattr(response, "stop_details", None))
            return None
        if response.stop_reason == "max_tokens":
            log.warning("[AI] Claude yaniti token sinirinda kesildi.")
            if output_format is not None:
                return None
        text = "".join(b.text for b in response.content if getattr(b, "type", "") == "text").strip()
        return text or None

    # ─── Yerel LLM ────────────────────────────────────────────────────────────

    def _local(self):
        if self._local_llm is None:
            return None
        try:
            llm = self._local_llm() if callable(self._local_llm) and not hasattr(self._local_llm, "generate") \
                else self._local_llm
        except Exception:
            return None
        return llm if llm is not None and getattr(llm, "available", False) else None

    # ─── Genel API ────────────────────────────────────────────────────────────

    def design(self, request: str) -> Tuple[Optional["dsg.Design"], str]:
        """İstekten tasarım üretir. Dönüş: (tasarım veya None, kaynak açıklaması)."""
        text = self._call_claude(
            system=DESIGN_SYSTEM,
            content=f"Kullanicinin sesli istegi: \"{request}\"\nBunu 3D baskiya uygun parcalar halinde tasarla.",
            effort="medium",
            max_tokens=16000,
            output_format={"type": "json_schema", "schema": dsg.DESIGN_SCHEMA},
        )
        if text:
            try:
                design = dsg.Design(json.loads(text), source="Claude")
                self.last_source = "Claude"
                return design, "Claude"
            except Exception as exc:
                log.warning("[AI] Claude tasarimi kullanilamadi: %s", exc)

        template = dsg.match_template(request)
        if template is not None:
            self.last_source = "sablon"
            return dsg.Design(template, source="sablon"), "sablon"

        llm = self._local()
        if llm is not None:
            try:
                raw = llm.generate(
                    f"Kullanicinin istegi: \"{request}\". Yalnizca su JSON semasina uyan tek bir JSON nesnesi dondur:\n"
                    + json.dumps(dsg.DESIGN_SCHEMA),
                    system=DESIGN_SYSTEM, temperature=0.2, max_tokens=4000,
                )
                data = _extract_json(raw)
                if data is not None:
                    self.last_source = "yerel LLM"
                    return dsg.Design(data, source="yerel LLM"), "yerel LLM"
            except Exception as exc:
                log.warning("[AI] Yerel LLM tasarimi kullanilamadi: %s", exc)
        return None, ""

    def ask(self, question: str, design: Optional["dsg.Design"] = None, step: int = -1) -> str:
        """Çizim/tasarım sorusuna kısa, sesli okunabilir Türkçe yanıt."""
        prompt = f"{_context_text(design, step)}\n\nKullanicinin sorusu: \"{question}\""
        answer = self._call_claude(system=HELP_SYSTEM, content=prompt, effort="low", max_tokens=2000)
        if answer:
            return answer
        llm = self._local()
        if llm is not None:
            try:
                answer = llm.generate(prompt, system=HELP_SYSTEM, temperature=0.3, max_tokens=200)
                if answer:
                    return answer
            except Exception as exc:
                log.debug("[AI] Yerel LLM yaniti alinamadi: %s", exc)
        return ""

    def review(self, png_bytes: bytes, design: Optional["dsg.Design"] = None, step: int = -1) -> str:
        """Ekran görüntüsündeki çizimi tasarım adımına göre değerlendirir (yalnızca Claude)."""
        content = [
            {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                          "data": base64.standard_b64encode(png_bytes).decode("ascii")}},
            {"type": "text", "text": (
                f"{_context_text(design, step)}\n\nEkran goruntusunde kullanicinin elle cizdigi cizgiler ve "
                "(varsa) yari saydam 3D rehber gorunuyor. Cizimi bu adima gore degerlendir: dogru olanlari kisaca "
                "soyle, eksik veya hatali olani ve bir sonraki yapmasi gerekeni soyle.")},
        ]
        return self._call_claude(system=HELP_SYSTEM, content=content, effort="low", max_tokens=2000) or ""
