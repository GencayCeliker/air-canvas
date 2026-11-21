"""air_canvas.ai — Tasarım asistanı (Claude API → yerel LLM → hazır şablonlar).

Claude kullanmak için ``pip install "air-canvas[ai]"`` kurun ve kimlik bilgisi verin:
    ANTHROPIC_API_KEY=...            (veya ``ant auth login`` profili)
    AIR_CANVAS_CLAUDE_MODEL=...      İsteğe bağlı model (varsayılan: claude-opus-5-5)

AIR_CANVAS_AI               Hangi yapay zekâ kullanılsın (``--ai`` ile de seçilir):
                            ``auto``     Claude → yerel LLM → şablon (varsayılan)
                            ``claude``   Claude → şablon
                            ``local``    yerel LLM → şablon; hiçbir veri internete gitmez
                            ``template`` yalnızca yerleşik şablonlar
AIR_CANVAS_LLM_TIMEOUT      Yerel modelin tasarım için bekleme süresi, sn (varsayılan: 300)
AIR_CANVAS_CLARIFY          ``0`` ise tasarımdan önce ölçü sorusu sorulmaz

Yerel LLM için LM Studio / Ollama (bkz. ``llm.py``) kullanılır.
"""

import base64
import json
import logging
import os
import re
from typing import Any, Callable, Dict, List, Optional, Tuple

from . import design as dsg
from .textutil import strip_reasoning

log = logging.getLogger(__name__)

DEFAULT_MODEL = "claude-opus-5-5"
FALLBACK_BETA = "server-side-fallback-2026-07-01"
AI_MODES = ("auto", "claude", "local", "template")

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

# Küçük yerel modeller şemayı tek örnekle çok daha iyi tutturur.
LOCAL_DESIGN_EXAMPLE = {
    "name": "Kalemlik",
    "summary": "60 milimetre çapında, 90 milimetre yüksek silindir kalemlik.",
    "parts": [{
        "name": "Gövde", "purpose": "Kalemleri tutar.",
        "step": "60 milimetrelik bir daire çiz, 90 milimetre yükselt. İçini 2 milimetre duvar kalacak şekilde oy.",
        "print_orientation": "Dik, taban tablada. PLA, yüzde 15 doluluk.",
        "shapes": [
            {"kind": "cylinder", "op": "add", "size": [60, 60, 90], "pos": [0, 0, 45], "rot": [0, 0, 0], "segments": 0},
            {"kind": "cylinder", "op": "subtract", "size": [56, 56, 90], "pos": [0, 0, 47], "rot": [0, 0, 0],
             "segments": 0},
        ],
        "placements": [{"pos": [0, 0, 0], "rot": [0, 0, 0]}],
    }],
    "print_tips": ["PLA yeterli.", "0.2 mm katman kullanın."],
}

LOCAL_DESIGN_RULES = """
Cikti kurallari: YALNIZCA tek bir JSON nesnesi dondur; aciklama, markdown veya kod blogu yazma.
Alanlar: name, summary, parts (name, purpose, step, print_orientation, shapes, placements), print_tips.
Her shape: kind (box|cylinder|sphere), op (add|subtract), size [3 sayi], pos [3 sayi], rot [3 sayi], segments (tamsayi).
Ornek bicim:
""" + json.dumps(LOCAL_DESIGN_EXAMPLE, ensure_ascii=False)

HELP_SYSTEM = """Sen Air Canvas'in 3D tasarim ve cizim asistanisin. Kullanici havada parmagiyla ciziyor ve seninle
sesli konusuyor. Cevaplarin sesli okunacak: Turkce, en fazla 3 kisa cumle, madde isareti veya markdown kullanma.
Olcu verirken milimetre kullan. 3D baskiya uygunluk (duvar kalinligi, sarkma, tolerans, malzeme) konusunda pratik ol."""

CLARIFY_SYSTEM = """Sen Air Canvas'in 3D baski tasarim asistanisin. Kullanici sesli olarak bir nesne tasarlamani istedi.
Tasarima baslamadan once karar ver: tasarimi BELIRGIN bicimde degistirecek kritik bir bilgi eksik mi?
(ana olcu veya boyut sinifi, ornegin dron icin pervane boyu; uyacagi hazir parcanin standardi; kac adet).
- Eksikse kullaniciya TEK, kisa, sesli okunacak Turkce bir soru sor; mumkunse ornek secenek ver
  ("5 inc mi 3 inc mi?" gibi). En fazla 2 bilgi iste.
- Istek yeterince acik veya makul varsayilanlar yeterliyse question bos string olsun.
JSON dondur: {"question": "..."}"""

CLARIFY_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {"question": {"type": "string"}},
    "required": ["question"],
    "additionalProperties": False,
}


def _context_text(design: Optional["dsg.Design"], step: int) -> str:
    if design is None:
        return "Su anda acik bir tasarim yok."
    lines = [design.overview_text()]
    if 0 <= step < len(design.parts):
        lines.append("Kullanicinin su anki adimi:\n" + design.step_text(step))
    return "\n".join(lines)


def _extract_json(text: str) -> Optional[Any]:
    """Serbest metindeki ilk JSON nesnesini ayıklar (yerel LLM çıktısı için)."""
    text = strip_reasoning(text or "")
    if not text:
        return None
    fenced = re.search(r"```(?:json)?\s*(\{.*\})\s*```", text, re.S)
    candidate = fenced.group(1) if fenced else text[text.find("{"): text.rfind("}") + 1]
    try:
        return json.loads(candidate)
    except Exception:
        return None


def _env_int(name: str, default: int) -> int:
    try:
        return int(os.getenv(name, "") or default)
    except ValueError:
        return default


class DesignAssistant:
    """Tasarım üretir, soruları yanıtlar, çizimi değerlendirir.

    local_llm: ``generate()`` ve ``available`` olan isteğe bağlı yerel istemci veya onu
               döndüren bir fonksiyon (her çağrıda güncel durumu almak için). ``chat()`` ve
               ``model_for()`` da varsa (``llm.LocalLLMClient``) işe uygun model, JSON şeması
               ve görsel mesajlar kullanılır.
    mode:      ``auto`` / ``claude`` / ``local`` / ``template`` (bkz. modül açıklaması).
    """

    def __init__(self, local_llm: Optional[Callable[[], Any]] = None, client: Any = None,
                 model: Optional[str] = None, use_claude: bool = True, mode: Optional[str] = None):
        self._local_llm = local_llm
        self.model = model or os.getenv("AIR_CANVAS_CLAUDE_MODEL") or DEFAULT_MODEL
        self._client = client
        mode = (mode or os.getenv("AIR_CANVAS_AI") or "auto").strip().lower()
        self.mode = mode if mode in AI_MODES else "auto"
        self._claude_disabled = not use_claude or self.mode in ("local", "template")
        self.clarify_enabled = os.getenv("AIR_CANVAS_CLARIFY", "1").strip() not in ("0", "false", "no", "hayir")
        self.local_timeout = float(_env_int("AIR_CANVAS_LLM_TIMEOUT", 300))
        self.last_source = ""
        # İsteğe bağlı ilerleme bildirimi: status_callback("Yerel model tasarlıyor ...")
        self.status_callback: Optional[Callable[[str], None]] = None

    def _status(self, text: str) -> None:
        if self.status_callback is not None:
            try:
                self.status_callback(text)
            except Exception:
                pass

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
        except TypeError as exc:
            # SDK, hiç kimlik bilgisi yoksa (ANTHROPIC_API_KEY / profil) istek anında TypeError fırlatır.
            if "authentication" in str(exc).lower():
                log.info("[AI] Claude kimlik bilgisi yok (ANTHROPIC_API_KEY); yerel LLM / sablon kullanilacak.")
                self._claude_disabled = True
                return None
            log.warning("[AI] Claude istegi basarisiz: %s", exc)
            return None
        except Exception as exc:   # Beklenmeyen her hata: tasarım yerel modele / şablona düşsün
            log.warning("[AI] Claude istegi basarisiz: %s", exc)
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
        if self._local_llm is None or self.mode in ("claude", "template"):
            return None
        try:
            llm = self._local_llm() if callable(self._local_llm) and not hasattr(self._local_llm, "generate") \
                else self._local_llm
        except Exception:
            return None
        return llm if llm is not None and getattr(llm, "available", False) else None

    @property
    def local_ready(self) -> bool:
        return self._local() is not None

    @staticmethod
    def _local_chat(llm, messages: List[Dict[str, Any]], task: str, **kwargs) -> str:
        """İşe uygun yerel modelle sohbet. Yalnızca ``generate`` olan istemcilerde metin mesajlarına düşer."""
        if hasattr(llm, "chat") and hasattr(llm, "model_for"):
            model = llm.model_for(task)
            if task == "vision" and not model:
                return ""   # Görsel destekli yerel model yok
            return strip_reasoning(llm.chat(messages, model=model or None, **kwargs) or "")
        if task == "vision":
            return ""
        system = "\n".join(m["content"] for m in messages if m["role"] == "system")
        convo = "\n\n".join(str(m["content"]) for m in messages if m["role"] != "system")
        return strip_reasoning(llm.generate(convo, system=system, temperature=kwargs.get("temperature", 0.3),
                                            max_tokens=kwargs.get("max_tokens", 400)) or "")

    def _local_design(self, llm, request: str) -> Optional["dsg.Design"]:
        """Yerel modelden JSON tasarım ister; geçersizse hatayı söyleyip bir kez daha dener."""
        messages: List[Dict[str, Any]] = [
            {"role": "system", "content": DESIGN_SYSTEM + "\n" + LOCAL_DESIGN_RULES},
            {"role": "user", "content": f"Kullanicinin sesli istegi: \"{request}\"\n"
                                        "Bunu 3D baskiya uygun parcalar halinde tasarla ve JSON dondur."},
        ]
        for attempt in range(2):
            self._status("Yerel model tasarliyor" + (" (tekrar deniyor)" if attempt else "") + " ...")
            try:
                raw = self._local_chat(llm, messages, "design", temperature=0.2, max_tokens=8000,
                                       timeout=self.local_timeout, json_schema=dsg.DESIGN_SCHEMA)
            except Exception as exc:
                log.warning("[AI] Yerel LLM tasarim istegi basarisiz: %s", exc)
                return None
            data = _extract_json(raw)
            if data is None:
                error = "gecerli bir JSON nesnesi yok"
            else:
                try:
                    return dsg.Design(data, source="yerel LLM")
                except Exception as exc:
                    error = str(exc)
            log.info("[AI] Yerel LLM tasarimi kullanilamadi (deneme %d): %s", attempt + 1, error)
            if not raw:
                break   # Sunucu cevap vermedi / zaman aşımı: tekrar denemek yalnızca bekletir.
            messages += [
                {"role": "assistant", "content": raw[:6000]},
                {"role": "user", "content": f"Bu cikti kullanilamadi ({error}). Ayni tasarimi semaya tam uyan, "
                                            "en az bir 'add' sekli olan gecerli tek bir JSON nesnesi olarak tekrar yaz."},
            ]
        return None

    # ─── Genel API ────────────────────────────────────────────────────────────

    def design(self, request: str) -> Tuple[Optional["dsg.Design"], str]:
        """İstekten tasarım üretir. Dönüş: (tasarım veya None, kaynak açıklaması).

        Sıra: Claude → yerel LLM → yerleşik şablon (``mode`` ile daraltılır)."""
        if self.mode in ("auto", "claude"):
            self._status("Claude tasarliyor ...")
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

        llm = self._local()
        if llm is not None:
            design = self._local_design(llm, request)
            if design is not None:
                self.last_source = "yerel LLM"
                return design, "yerel LLM"

        template = dsg.match_template(request)
        if template is not None:
            self.last_source = "sablon"
            return dsg.Design(template, source="sablon"), "sablon"
        return None, ""

    def clarify(self, request: str) -> str:
        """Tasarım için kritik bir ölçü eksikse kullanıcıya sorulacak tek soruyu döndürür (yoksa "").

        Yalnızca bir yapay zekâ tasarlayacaksa sorulur; ölçü içeren isteklerde (rakam) sorulmaz."""
        if not self.clarify_enabled or self.mode == "template" or re.search(r"\d", request or ""):
            return ""
        prompt = f"Kullanicinin sesli istegi: \"{request}\""
        raw: Optional[str] = None
        if self.mode in ("auto", "claude"):
            raw = self._call_claude(system=CLARIFY_SYSTEM, content=prompt, effort="low", max_tokens=1000,
                                    output_format={"type": "json_schema", "schema": CLARIFY_SCHEMA})
        if raw is None:
            llm = self._local()
            if llm is None:
                return ""
            try:
                raw = self._local_chat(llm, [{"role": "system", "content": CLARIFY_SYSTEM},
                                             {"role": "user", "content": prompt}],
                                       "chat", temperature=0.2, max_tokens=600, timeout=60,
                                       json_schema=CLARIFY_SCHEMA)
            except Exception as exc:
                log.debug("[AI] Yerel LLM soru uretemedi: %s", exc)
                return ""
        data = _extract_json(raw or "")
        question = str(data.get("question") or "").strip() if isinstance(data, dict) else ""
        return question[:240]

    def ask(self, question: str, design: Optional["dsg.Design"] = None, step: int = -1) -> str:
        """Çizim/tasarım sorusuna kısa, sesli okunabilir Türkçe yanıt."""
        prompt = f"{_context_text(design, step)}\n\nKullanicinin sorusu: \"{question}\""
        if self.mode in ("auto", "claude"):
            answer = self._call_claude(system=HELP_SYSTEM, content=prompt, effort="low", max_tokens=2000)
            if answer:
                return answer
        llm = self._local()
        if llm is not None:
            try:
                answer = self._local_chat(llm, [{"role": "system", "content": HELP_SYSTEM},
                                                {"role": "user", "content": prompt}],
                                          "chat", temperature=0.3, max_tokens=800)
                if answer:
                    return answer
            except Exception as exc:
                log.debug("[AI] Yerel LLM yaniti alinamadi: %s", exc)
        return ""

    def review(self, png_bytes: bytes, design: Optional["dsg.Design"] = None, step: int = -1) -> str:
        """Ekran görüntüsündeki çizimi tasarım adımına göre değerlendirir (Claude → yerel görsel model)."""
        instruction = (
            f"{_context_text(design, step)}\n\nEkran goruntusunde kullanicinin elle cizdigi cizgiler ve "
            "(varsa) yari saydam 3D rehber gorunuyor. Cizimi bu adima gore degerlendir: dogru olanlari kisaca "
            "soyle, eksik veya hatali olani ve bir sonraki yapmasi gerekeni soyle.")
        if self.mode in ("auto", "claude"):
            content = [
                {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                              "data": base64.standard_b64encode(png_bytes).decode("ascii")}},
                {"type": "text", "text": instruction},
            ]
            answer = self._call_claude(system=HELP_SYSTEM, content=content, effort="low", max_tokens=2000)
            if answer:
                return answer
        llm = self._local()
        if llm is not None and hasattr(llm, "image_message"):
            try:
                return self._local_chat(llm, [{"role": "system", "content": HELP_SYSTEM},
                                              llm.image_message(instruction, png_bytes)],
                                        "vision", temperature=0.3, max_tokens=800, timeout=180)
            except Exception as exc:
                log.debug("[AI] Yerel gorsel model yanit vermedi: %s", exc)
        return ""
