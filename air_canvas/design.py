"""air_canvas.design — 3D baskıya uygun parça tasarımları.

Bir tasarım; her biri basit katılardan (kutu, silindir/koni/altıgen, küre) oluşan
parçalardan meydana gelir. Katılar eklenir (``add``) veya çıkarılır (``subtract``);
böylece delikler, yuvalar ve cepler tanımlanabilir. Katı modelleme ``manifold3d`` ile
yapılır; çıktı su geçirmez (manifold) olduğundan dilimleyicilerde sorunsuz açılır.

Koordinatlar milimetredir, Z yukarıdır. Her parçanın şekilleri parçanın kendi
koordinatlarında ve **baskı yönünde** verilir (taban z=0 düzlemine oturur).
``placements`` parçanın montajdaki konum(lar)ını belirtir; her konum bir baskı adedidir.
Şekiller listedeki sırayla uygulanır (ekle / çıkar / tekrar ekle ...).

Tasarım sözlüğü (AI'dan veya hazır şablonlardan gelir)::

    {
      "name": "Dron kolu",
      "summary": "...",
      "parts": [
        {"name": "...", "purpose": "...", "step": "...", "print_orientation": "...",
         "shapes": [{"kind": "box|cylinder|sphere", "op": "add|subtract",
                     "size": [x, y, z], "pos": [x, y, z], "rot": [rx, ry, rz],
                     "segments": 0}],
         "placements": [{"pos": [x, y, z], "rot": [rx, ry, rz]}]}
      ],
      "print_tips": ["..."]
    }

``size`` anlamı: kutu → [en, boy, yükseklik]; silindir → [alt çap, üst çap, yükseklik]
(üst çap 0 ise koni); küre → [çap x, çap y, çap z]. ``segments``: 0 = pürüzsüz,
6 = altıgen (somun/vida başı), 4 = kare vb.
"""

import json
import logging
import math
import os
import re
import struct
import time
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

try:
    from manifold3d import Manifold
    MANIFOLD_OK = True
except Exception:  # pragma: no cover - isteğe bağlı bağımlılık
    Manifold = None
    MANIFOLD_OK = False

log = logging.getLogger(__name__)

MAX_PARTS = 12
MAX_SHAPES = 60
MAX_PLACEMENTS = 8
MIN_DIM, MAX_DIM = 0.2, 400.0
MAX_POS = 600.0
SMOOTH_SEGMENTS = 48

# ─── Yapılandırılmış çıktı şeması (Claude structured outputs) ─────────────────

_VEC3 = {"type": "array", "items": {"type": "number"}}

DESIGN_SCHEMA: Dict[str, Any] = {
    "type": "object",
    "properties": {
        "name": {"type": "string"},
        "summary": {"type": "string"},
        "parts": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "name": {"type": "string"},
                    "purpose": {"type": "string"},
                    "step": {"type": "string"},
                    "print_orientation": {"type": "string"},
                    "shapes": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {
                                "kind": {"type": "string", "enum": ["box", "cylinder", "sphere"]},
                                "op": {"type": "string", "enum": ["add", "subtract"]},
                                "size": _VEC3,
                                "pos": _VEC3,
                                "rot": _VEC3,
                                "segments": {"type": "integer"},
                            },
                            "required": ["kind", "op", "size", "pos", "rot", "segments"],
                            "additionalProperties": False,
                        },
                    },
                    "placements": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"pos": _VEC3, "rot": _VEC3},
                            "required": ["pos", "rot"],
                            "additionalProperties": False,
                        },
                    },
                },
                "required": ["name", "purpose", "step", "print_orientation", "shapes", "placements"],
                "additionalProperties": False,
            },
        },
        "print_tips": {"type": "array", "items": {"type": "string"}},
    },
    "required": ["name", "summary", "parts", "print_tips"],
    "additionalProperties": False,
}


# ─── Doğrulama ────────────────────────────────────────────────────────────────

def _vec3(value, default=0.0, lo=-MAX_POS, hi=MAX_POS) -> List[float]:
    try:
        items = list(value)[:3]
    except TypeError:
        items = []
    vals = []
    for item in items:
        try:
            v = float(item)
        except (TypeError, ValueError):
            v = default
        vals.append(max(lo, min(hi, v)) if math.isfinite(v) else default)
    while len(vals) < 3:
        vals.append(default)
    return vals


def normalize_design(data: Any) -> Optional[Dict[str, Any]]:
    """AI'dan gelen tasarımı güvenli sınırlara çeker; kullanılamazsa ``None`` döner."""
    if not isinstance(data, dict):
        return None
    parts_out = []
    for part in list(data.get("parts") or [])[:MAX_PARTS]:
        if not isinstance(part, dict):
            continue
        shapes = []
        for sh in list(part.get("shapes") or [])[:MAX_SHAPES]:
            if not isinstance(sh, dict):
                continue
            kind = sh.get("kind")
            if kind not in ("box", "cylinder", "sphere"):
                continue
            size = _vec3(sh.get("size"), MIN_DIM, 0.0, MAX_DIM)
            if kind == "cylinder":
                # Alt veya üst çaptan biri 0 olabilir (koni), yükseklik olamaz.
                if size[0] < MIN_DIM and size[1] < MIN_DIM:
                    continue
                size[2] = max(MIN_DIM, size[2])
            else:
                size = [max(MIN_DIM, s) for s in size]
            try:
                segments = int(sh.get("segments") or 0)
            except (TypeError, ValueError):
                segments = 0
            segments = 0 if segments < 3 else min(segments, 128)
            shapes.append({
                "kind": kind,
                "op": "subtract" if sh.get("op") == "subtract" else "add",
                "size": size,
                "pos": _vec3(sh.get("pos")),
                "rot": _vec3(sh.get("rot"), 0.0, -360.0, 360.0),
                "segments": segments,
            })
        if not any(s["op"] == "add" for s in shapes):
            continue
        placements = []
        for pl in list(part.get("placements") or [])[:MAX_PLACEMENTS]:
            if isinstance(pl, dict):
                placements.append({"pos": _vec3(pl.get("pos")), "rot": _vec3(pl.get("rot"), 0.0, -360.0, 360.0)})
        if not placements:
            placements = [{"pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0]}]
        parts_out.append({
            "name": str(part.get("name") or f"Parça {len(parts_out) + 1}")[:60],
            "purpose": str(part.get("purpose") or "")[:300],
            "step": str(part.get("step") or "")[:600],
            "print_orientation": str(part.get("print_orientation") or "")[:200],
            "shapes": shapes,
            "placements": placements,
        })
    if not parts_out:
        return None
    return {
        "name": str(data.get("name") or "Tasarım")[:60],
        "summary": str(data.get("summary") or "")[:600],
        "parts": parts_out,
        "print_tips": [str(t)[:300] for t in list(data.get("print_tips") or [])[:10]],
    }


# ─── Katı modelleme ───────────────────────────────────────────────────────────

def _shape_solid(sh: Dict[str, Any]):
    sx, sy, sz = sh["size"]
    seg = sh["segments"] or SMOOTH_SEGMENTS
    if sh["kind"] == "box":
        solid = Manifold.cube([sx, sy, sz], True)
    elif sh["kind"] == "cylinder":
        r_low, r_high = sx / 2.0, sy / 2.0
        if r_low <= 0:
            # manifold3d alt yarıçapın sıfır olmasını sevmez: ters koni üretip çevir.
            solid = Manifold.cylinder(sz, r_high, 0.0, seg, True).rotate([180.0, 0.0, 0.0])
        else:
            solid = Manifold.cylinder(sz, r_low, r_high, seg, True)
    else:
        base = max(sx, sy, sz)
        solid = Manifold.sphere(base / 2.0, seg).scale([sx / base, sy / base, sz / base])
    if any(sh["rot"]):
        solid = solid.rotate(list(sh["rot"]))
    return solid.translate(list(sh["pos"]))


def build_part(part: Dict[str, Any]):
    """Parçanın katı modelini (parça koordinatlarında) üretir."""
    if not MANIFOLD_OK:
        raise RuntimeError("3D modelleme için manifold3d gerekiyor: pip install manifold3d")
    # Şekiller yazıldıkları sırayla uygulanır: bir boşluk açıldıktan sonra eklenen
    # şekiller (ör. ızgara halkaları) korunur. Baştaki çıkarmalar boşa düşer.
    solid = None
    for sh in part["shapes"]:
        piece = _shape_solid(sh)
        if sh["op"] == "add":
            solid = piece if solid is None else solid + piece
        elif solid is not None:
            solid = solid - piece
    return solid


def mesh_arrays(solid) -> Tuple[np.ndarray, np.ndarray]:
    mesh = solid.to_mesh()
    verts = np.asarray(mesh.vert_properties, dtype=np.float64)[:, :3]
    tris = np.asarray(mesh.tri_verts, dtype=np.int64)
    return verts, tris


def _rotation_matrix(rot: List[float]) -> np.ndarray:
    """manifold3d ile aynı sıra: önce X, sonra Y, sonra Z ekseni (derece)."""
    rx, ry, rz = (math.radians(a) for a in rot)
    cx, sx, cy, sy, cz, sz = math.cos(rx), math.sin(rx), math.cos(ry), math.sin(ry), math.cos(rz), math.sin(rz)
    mx = np.array([[1, 0, 0], [0, cx, -sx], [0, sx, cx]])
    my = np.array([[cy, 0, sy], [0, 1, 0], [-sy, 0, cy]])
    mz = np.array([[cz, -sz, 0], [sz, cz, 0], [0, 0, 1]])
    return mz @ my @ mx


class Design:
    """Doğrulanmış tasarım + önbelleğe alınmış parça ağları."""

    def __init__(self, data: Dict[str, Any], source: str = ""):
        norm = normalize_design(data)
        if norm is None:
            raise ValueError("Tasarım geçersiz veya boş.")
        self.data = norm
        self.source = source
        self.name: str = norm["name"]
        self.summary: str = norm["summary"]
        self.parts: List[Dict[str, Any]] = norm["parts"]
        self.print_tips: List[str] = norm["print_tips"]
        self.meshes: List[Tuple[np.ndarray, np.ndarray]] = []
        self.failed_parts: List[str] = []
        for part in self.parts:
            try:
                solid = build_part(part)
                if solid.is_empty():
                    raise ValueError("boş katı")
                self.meshes.append(mesh_arrays(solid))
            except Exception as exc:
                log.warning("[Design] '%s' parçası üretilemedi: %s", part["name"], exc)
                self.failed_parts.append(part["name"])
                self.meshes.append((np.zeros((0, 3)), np.zeros((0, 3), np.int64)))
        if all(len(t) == 0 for _, t in self.meshes):
            raise ValueError("Hiçbir parça üretilemedi.")

    def quantity(self, index: int) -> int:
        return len(self.parts[index]["placements"])

    def part_size(self, index: int) -> Tuple[float, float, float]:
        verts = self.meshes[index][0]
        if not len(verts):
            return (0.0, 0.0, 0.0)
        ext = verts.max(axis=0) - verts.min(axis=0)
        return (float(ext[0]), float(ext[1]), float(ext[2]))

    def placed_meshes(self) -> List[Tuple[int, np.ndarray, np.ndarray]]:
        """Montaj konumundaki ağlar: (parça indeksi, köşeler, üçgenler)."""
        out = []
        for i, part in enumerate(self.parts):
            verts, tris = self.meshes[i]
            if not len(tris):
                continue
            for pl in part["placements"]:
                placed = verts @ _rotation_matrix(pl["rot"]).T + np.array(pl["pos"])
                out.append((i, placed, tris))
        return out

    def overview_text(self) -> str:
        names = ", ".join(f"{p['name']}" + (f" ({len(p['placements'])} adet)" if len(p["placements"]) > 1 else "")
                          for p in self.parts)
        return f"{self.name}: {self.summary} Parçalar: {names}."

    def step_text(self, index: int) -> str:
        part = self.parts[index]
        qty = self.quantity(index)
        x, y, z = self.part_size(index)
        lines = [f"Adım {index + 1}/{len(self.parts)}: {part['name']}" + (f" x{qty}" if qty > 1 else "")]
        if part["step"]:
            lines.append(part["step"])
        lines.append(f"Ölçü: {x:.1f} x {y:.1f} x {z:.1f} mm")
        if part["print_orientation"]:
            lines.append(f"Baskı: {part['print_orientation']}")
        return "\n".join(lines)

    def spoken_step(self, index: int) -> str:
        """Adımın sesli okunacak hâli (başlık + talimat)."""
        part = self.parts[index]
        qty = self.quantity(index)
        head = f"Adım {index + 1}: {part['name']}" + (f", {qty} adet." if qty > 1 else ".")
        return f"{head} {part['step']}".strip()


# ─── STL dışa aktarma ─────────────────────────────────────────────────────────

def write_stl(path: str, verts: np.ndarray, tris: np.ndarray, name: str = "air_canvas") -> None:
    """İkili (binary) STL yazar; Unicode yollarda da çalışır."""
    tri_pts = verts[tris].astype(np.float32)                     # (n, 3, 3)
    normals = np.cross(tri_pts[:, 1] - tri_pts[:, 0], tri_pts[:, 2] - tri_pts[:, 0])
    lengths = np.linalg.norm(normals, axis=1, keepdims=True)
    normals = np.divide(normals, lengths, out=np.zeros_like(normals), where=lengths > 0)
    record = np.zeros(len(tris), dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("attr", "<u2")])
    record["n"] = normals
    record["v"] = tri_pts
    # Başlık ASCII olmalı ve "solid" ile başlamamalı (bazı okuyucular ASCII STL sanar).
    header = f"air_canvas {slugify(name)}".encode("ascii")[:80].ljust(80, b" ")
    with open(path, "wb") as fh:
        fh.write(header)
        fh.write(struct.pack("<I", len(tris)))
        fh.write(record.tobytes())


def slugify(text: str) -> str:
    table = str.maketrans("çğıöşüÇĞİÖŞÜ", "cgiosuCGIOSU")
    slug = re.sub(r"[^a-z0-9]+", "_", (text or "").translate(table).lower()).strip("_")
    return slug[:40] or "tasarim"


def export_design(design: Design, out_root: str) -> str:
    """Her parçayı baskı tablasına oturtulmuş ayrı STL olarak, montaj önizlemesini ve
    talimatları bir klasöre yazar. Klasör yolunu döndürür."""
    folder = os.path.join(out_root, f"{slugify(design.name)}_{time.strftime('%Y%m%d_%H%M%S')}")
    suffix = 1
    base_folder = folder
    while os.path.exists(folder):
        folder = f"{base_folder}_{suffix}"
        suffix += 1
    os.makedirs(folder)

    for i, part in enumerate(design.parts):
        verts, tris = design.meshes[i]
        if not len(tris):
            continue
        # Tablaya oturt: taban z=0, XY ortada.
        mins, maxs = verts.min(axis=0), verts.max(axis=0)
        offset = np.array([-(mins[0] + maxs[0]) / 2, -(mins[1] + maxs[1]) / 2, -mins[2]])
        qty = design.quantity(i)
        fname = f"{i + 1:02d}_{slugify(part['name'])}" + (f"_x{qty}" if qty > 1 else "") + ".stl"
        write_stl(os.path.join(folder, fname), verts + offset, tris, part["name"])

    placed = design.placed_meshes()
    if placed:
        all_v, all_t, base = [], [], 0
        for _, verts, tris in placed:
            all_v.append(verts)
            all_t.append(tris + base)
            base += len(verts)
        write_stl(os.path.join(folder, "montaj_onizleme.stl"), np.vstack(all_v), np.vstack(all_t), design.name)

    with open(os.path.join(folder, "talimatlar.txt"), "w", encoding="utf-8") as fh:
        fh.write(f"{design.name}\n{'=' * len(design.name)}\n\n{design.summary}\n\n")
        for i in range(len(design.parts)):
            fh.write(design.step_text(i) + "\n")
            if design.parts[i]["purpose"]:
                fh.write(f"Görevi: {design.parts[i]['purpose']}\n")
            fh.write("\n")
        if design.print_tips:
            fh.write("Baskı ipuçları\n--------------\n" + "\n".join(f"- {t}" for t in design.print_tips) + "\n")
        if design.failed_parts:
            fh.write("\nÜretilemeyen parçalar: " + ", ".join(design.failed_parts) + "\n")
        fh.write("\nmontaj_onizleme.stl yalnızca görsel kontrol içindir; baskı için numaralı dosyaları kullanın.\n")
    with open(os.path.join(folder, "tasarim.json"), "w", encoding="utf-8") as fh:
        json.dump(design.data, fh, ensure_ascii=False, indent=2)
    return folder


# ─── 3D önizleme (izometrik, gölgeli) ─────────────────────────────────────────

_ISO_COS, _ISO_SIN = math.cos(math.radians(30)), math.sin(math.radians(30))
_LIGHT = np.array([0.35, 0.55, 1.0]) / np.linalg.norm([0.35, 0.55, 1.0])
_VIEW = np.array([1.0, 1.0, 1.0]) / math.sqrt(3)
PART_COLORS = [(80, 170, 255), (120, 220, 120), (255, 170, 90), (200, 130, 255),
               (90, 220, 230), (240, 120, 160), (180, 200, 90), (160, 160, 255)]
HIGHLIGHT = (40, 200, 255)
DIM_GRAY = (120, 120, 120)


def _project(verts: np.ndarray) -> np.ndarray:
    sx = (verts[:, 0] - verts[:, 1]) * _ISO_COS
    sy = (verts[:, 0] + verts[:, 1]) * _ISO_SIN - verts[:, 2]
    return np.stack([sx, sy], axis=1)


def render_preview(design: Design, width: int, height: int, highlight: Optional[int] = None,
                   only_highlight: bool = False) -> np.ndarray:
    """Tasarımı gölgeli izometrik görünüm olarak çizer (BGR, siyah arka plan = boş).

    highlight: vurgulanacak parça (diğerleri gri). only_highlight: yalnızca o parçayı çiz.
    """
    import cv2  # yerel import: modül OpenCV olmadan da test edilebilsin

    img = np.zeros((height, width, 3), np.uint8)
    items = design.placed_meshes()
    if only_highlight and highlight is not None:
        verts, tris = design.meshes[highlight]
        items = [(highlight, verts, tris)] if len(tris) else []
    if not items:
        return img

    all_pts = np.vstack([_project(v) for _, v, _ in items])
    mins, maxs = all_pts.min(axis=0), all_pts.max(axis=0)
    span = np.maximum(maxs - mins, 1e-6)
    margin = 24
    scale = min((width - 2 * margin) / span[0], (height - 2 * margin) / span[1])
    offset = np.array([width, height]) / 2 - (mins + maxs) / 2 * scale

    polys, depths, colors = [], [], []
    for idx, verts, tris in items:
        tri_v = verts[tris]
        normals = np.cross(tri_v[:, 1] - tri_v[:, 0], tri_v[:, 2] - tri_v[:, 0])
        lens = np.linalg.norm(normals, axis=1)
        ok = lens > 1e-12
        normals[ok] /= lens[ok, None]
        visible = ok & (normals @ _VIEW > 0)
        if not visible.any():
            continue
        shade = 0.35 + 0.65 * np.clip(normals[visible] @ _LIGHT, 0, 1)
        if highlight is None:
            base = np.array(PART_COLORS[idx % len(PART_COLORS)], float)
        elif idx == highlight:
            base = np.array(HIGHLIGHT, float)
        else:
            base = np.array(DIM_GRAY, float)
        pts2d = (_project(tri_v[visible].reshape(-1, 3)) * scale + offset).reshape(-1, 3, 2)
        polys.append(np.round(pts2d).astype(np.int32))
        depths.append(tri_v[visible].sum(axis=(1, 2)))   # x+y+z toplamı ≈ izleyiciye yakınlık
        colors.append(shade[:, None] * base[None, :])
    if not polys:
        return img

    polys_all = np.concatenate(polys)
    order = np.argsort(np.concatenate(depths))          # uzaktan yakına (ressam algoritması)
    colors_all = np.concatenate(colors)
    for k in order:
        c = colors_all[k]
        cv2.fillConvexPoly(img, polys_all[k], (int(c[0]) or 1, int(c[1]) or 1, int(c[2]) or 1), cv2.LINE_AA)
    return img


# ─── Hazır şablonlar (AI yokken de çalışır) ──────────────────────────────────
# Metinler sesli okunduğu için doğru Türkçe yazımla tutulur; ekranda ASCII'ye çevrilir.

def _s(kind, size, pos=(0, 0, 0), op="add", rot=(0, 0, 0), segments=0):
    return {"kind": kind, "op": op, "size": list(size), "pos": list(pos), "rot": list(rot), "segments": segments}


def _hex_d(across_flats: float) -> float:
    """Altıgen anahtar ağzından köşeden köşeye çap."""
    return across_flats / math.cos(math.radians(30))


_ISO_HEX = {2: 4.0, 2.5: 5.0, 3: 5.5, 4: 7.0, 5: 8.0, 6: 10.0, 8: 13.0}


def _one(pos=(0, 0, 0), rot=(0, 0, 0)) -> List[Dict[str, Any]]:
    return [{"pos": list(pos), "rot": list(rot)}]


def template_bolt(m: float = 3, length: float = 12) -> Dict[str, Any]:
    m = m if m in _ISO_HEX else 3
    af = _ISO_HEX[m]
    head_h = round(0.7 * m, 1)
    return {
        "name": f"M{m:g}x{length:g} altıgen başlı cıvata",
        "summary": f"M{m:g} çapında, {length:g} milimetre boyunda altıgen başlı cıvata. Baş aşağıda basılır, destek gerekmez.",
        "parts": [{
            "name": f"M{m:g} cıvata",
            "purpose": "Parçaları birbirine bağlar.",
            "step": (f"Önce {af:g} milimetre anahtar ağızlı altıgen başı çiz, yüksekliği {head_h:g} milimetre. "
                     f"Sonra ortasından yukarı {m:g} milimetre çapında, {length:g} milimetre boyunda gövdeyi çiz. "
                     "Ucuna yarım milimetrelik pah ver ki somun kolay girsin."),
            "print_orientation": "Baş tablaya oturur, gövde yukarı bakar. Yüzde yüz doluluk.",
            "shapes": [
                _s("cylinder", (_hex_d(af), _hex_d(af), head_h), (0, 0, head_h / 2), segments=6),
                _s("cylinder", (m * 0.97, m * 0.97, length - 0.5), (0, 0, head_h + (length - 0.5) / 2)),
                _s("cylinder", (m * 0.97, m * 0.97 - 1.0, 0.5), (0, 0, head_h + length - 0.25)),
            ],
            "placements": _one(),
        }],
        "print_tips": [
            "FDM yazıcıda küçük vida dişi iyi çıkmaz: dişi baskıdan sonra pafta ile açın veya gövdeyi pim olarak kullanın.",
            "0.12 mm katman yüksekliği ve yüzde yüz doluluk kullanın.",
            "Gerçek yük taşıyacaksa çelik cıvata tercih edin.",
        ],
    }


def template_nut(m: float = 3) -> Dict[str, Any]:
    m = m if m in _ISO_HEX else 3
    af = _ISO_HEX[m]
    h = round(0.8 * m, 1)
    return {
        "name": f"M{m:g} altıgen somun",
        "summary": f"M{m:g} cıvataya uyan altıgen somun; deliği baskı toleransı için 0.3 milimetre geniş.",
        "parts": [{
            "name": f"M{m:g} somun",
            "purpose": "Cıvatayı sabitler.",
            "step": (f"{af:g} milimetre anahtar ağızlı bir altıgen çiz, kalınlığı {h:g} milimetre. "
                     f"Ortasına {m + 0.3:g} milimetre çapında delik aç."),
            "print_orientation": "Düz yüzey tablada. Yüzde yüz doluluk.",
            "shapes": [
                _s("cylinder", (_hex_d(af), _hex_d(af), h), (0, 0, h / 2), segments=6),
                _s("cylinder", (m + 0.3, m + 0.3, h + 2), (0, 0, h / 2), op="subtract"),
            ],
            "placements": _one(),
        }],
        "print_tips": [f"Dişleri baskıdan sonra M{m:g} kılavuz ile açın.", "0.12 mm katman, yüzde yüz doluluk."],
    }


def _motor_holes(cx: float, z: float, spacing: float, d: float = 3.2) -> List[Dict[str, Any]]:
    r = spacing / 2
    return [_s("cylinder", (d, d, z * 3), (cx + dx, dy, z / 2), op="subtract")
            for dx, dy in ((r, 0), (-r, 0), (0, r), (0, -r))]


def _arm_shapes(length: float = 110.0) -> List[Dict[str, Any]]:
    t = 6.0
    return [
        _s("box", (length, 14, t), (length / 2, 0, t / 2)),
        _s("cylinder", (28, 28, t), (length, 0, t / 2)),
        _s("cylinder", (8, 8, t * 3), (length, 0, t / 2), op="subtract"),
        *_motor_holes(length, t, 16.0),
        _s("cylinder", (3.2, 3.2, t * 3), (8, 0, t / 2), op="subtract"),
        _s("cylinder", (3.2, 3.2, t * 3), (22, 0, t / 2), op="subtract"),
    ]


_ARM_STEP = ("110 milimetre uzunluğunda, 14 milimetre genişliğinde dikdörtgen kolu çiz. "
             "Ucuna 28 milimetre çapında motor tablasını ekle; ortasına 8 milimetre mil deliği, "
             "etrafına 16 milimetre aralıklı dört M3 delik aç. Kök tarafına gövdeye bağlanan iki M3 delik ekle.")


def template_drone_arm() -> Dict[str, Any]:
    return {
        "name": "Dron kolu",
        "summary": "5 inç pervaneli dron için 110 milimetre kol; ucunda 2204-2306 motor yuvası, 16 milimetre delik aralığı.",
        "parts": [{
            "name": "Kol",
            "purpose": "Motoru gövdeye bağlar.",
            "step": _ARM_STEP,
            "print_orientation": "Düz yatar, delikler dik. PETG veya karbon katkılı naylon, yüzde 40 doluluk, 4 çevre duvarı.",
            "shapes": _arm_shapes(),
            "placements": _one(),
        }],
        "print_tips": ["Kollar titreşim alır: PLA yerine PETG veya naylon kullanın.", "4 çevre duvarı ve yüzde 40 gyroid doluluk."],
    }


def template_drone() -> Dict[str, Any]:
    plate = 70.0
    holes = [_s("cylinder", (3.2, 3.2, 10), (x, y, 1.5), op="subtract")
             for x in (-15.25, 15.25) for y in (-15.25, 15.25)]
    # Kol kökü delikleri: kollar köşegenlere, merkezden (plate/2 - 12) mm uzağa yerleşir.
    arm_root = [_s("cylinder", (3.2, 3.2, 10), (r * math.cos(math.radians(a)), r * math.sin(math.radians(a)), 1.5),
                   op="subtract")
                for a in (45, 135, 225, 315) for r in (plate / 2 - 4, plate / 2 + 10)]
    arms = [{"pos": [(plate / 2 - 12) * math.cos(math.radians(a)), (plate / 2 - 12) * math.sin(math.radians(a)), 3],
             "rot": [0, 0, a]} for a in (45, 135, 225, 315)]
    return {
        "name": "Mini dron gövdesi",
        "summary": ("5 inç pervaneli, X tipi dört kollu dron çerçevesi. Uçuş kontrolcüsü yuvalı alt plaka, "
                    "dört kol, üst kapak ve dört ayaktan oluşuyor."),
        "parts": [
            {
                "name": "Alt plaka",
                "purpose": "Kolları ve uçuş kontrolcüsünü taşır.",
                "step": ("Önce 70 milimetrelik kare alt plakayı çiz, kalınlığı 3 milimetre. Ortasına 30.5 milimetre "
                         "aralıklı dört M3 delik aç; bunlar uçuş kontrolcüsü için. Köşegenlere kollar için ikişer delik ekle."),
                "print_orientation": "Düz, tablaya yatık. PETG, yüzde 50 doluluk.",
                "shapes": [_s("box", (plate, plate, 3), (0, 0, 1.5)), *holes] + [
                    s for s in arm_root if abs(s["pos"][0]) < plate / 2 - 2],
                "placements": _one(),
            },
            {
                "name": "Kol",
                "purpose": "Motorları taşır.",
                "step": _ARM_STEP + " Bundan dört tane basacağız, köşegenlere yerleşecek.",
                "print_orientation": "Düz yatar. PETG veya naylon, 4 çevre duvarı.",
                "shapes": _arm_shapes(),
                "placements": arms,
            },
            {
                "name": "Üst kapak",
                "purpose": "Elektroniği korur; kamera ve anten bağlanır.",
                "step": ("Üst kapağı çiz: 60 milimetre kare, 2 milimetre kalın. Ortasına kablo geçişi için "
                         "20 milimetrelik delik aç ve köşelere dört M3 delik ekle."),
                "print_orientation": "Düz yatar. PLA yeterli.",
                "shapes": [
                    _s("box", (60, 60, 2), (0, 0, 1)),
                    _s("cylinder", (20, 20, 6), (0, 0, 1), op="subtract"),
                    *[_s("cylinder", (3.2, 3.2, 6), (x, y, 1), op="subtract") for x in (-25, 25) for y in (-25, 25)],
                ],
                "placements": _one((0, 0, 28)),
            },
            {
                "name": "Ayak",
                "purpose": "İnişte gövdeyi korur, plakalar arasında ara parça görevi görür.",
                "step": ("Son olarak ayağı çiz: 10 milimetre çapında, 25 milimetre yüksek silindir. "
                         "Ortasından M3 cıvata geçsin diye 3.2 milimetre delik aç. Dört tane basılacak."),
                "print_orientation": "Dik durur. TPU ile basarsanız darbeyi emer.",
                "shapes": [
                    _s("cylinder", (10, 10, 25), (0, 0, 12.5)),
                    _s("cylinder", (3.2, 3.2, 30), (0, 0, 12.5), op="subtract"),
                ],
                "placements": [{"pos": [x, y, 3], "rot": [0, 0, 0]} for x in (-25, 25) for y in (-25, 25)],
            },
        ],
        "print_tips": [
            "Kolları PETG veya naylonla, ayakları TPU ile basın.",
            "Delikler 3.2 milimetre: M3 cıvata rahat geçer.",
            "Montaj: kollar alt plaka ile üst kapak arasına sıkışır, ayaklar ara parça görevi görür.",
        ],
    }


def _wheel_shapes(d: float = 40.0, w: float = 14.0, axle: float = 3.2) -> List[Dict[str, Any]]:
    shapes = [
        _s("cylinder", (d, d, w), (0, 0, w / 2)),
        _s("cylinder", (d - 8, d - 8, w - 4), (0, 0, w / 2 + 2 + 0.01), op="subtract"),
        _s("cylinder", (10, 10, w), (0, 0, w / 2)),
    ]
    for a in range(0, 360, 60):
        shapes.append(_s("box", ((d - 8) / 2, 3, w - 2), ((d - 8) / 4 * math.cos(math.radians(a)),
                                                           (d - 8) / 4 * math.sin(math.radians(a)), (w - 2) / 2),
                         rot=(0, 0, a)))
    shapes.append(_s("cylinder", (axle, axle, w * 3), (0, 0, w / 2), op="subtract"))
    for k in range(24):  # Lastik tırtılları
        a = k * 15
        shapes.append(_s("box", (1.2, 2.0, w), ((d / 2) * math.cos(math.radians(a)), (d / 2) * math.sin(math.radians(a)), w / 2),
                         op="subtract", rot=(0, 0, a)))
    return shapes


_WHEEL_STEP = ("40 milimetrelik bir daire çiz, bu lastiğin dış çizgisi. İçine 32 milimetrelik jant boşluğu, "
               "ortasına 10 milimetrelik göbek ve 3.2 milimetrelik aks deliği ekle. Göbekten jante altı kol çiz.")


def template_wheel() -> Dict[str, Any]:
    return {
        "name": "Model araba tekerleği",
        "summary": "40 milimetre çapında, 14 milimetre genişliğinde, altı kollu jant ve tırtıklı lastik; 3 milimetre aks için.",
        "parts": [{
            "name": "Tekerlek",
            "purpose": "Aracı taşır ve yuvarlar.",
            "step": _WHEEL_STEP,
            "print_orientation": "Düz yüzü tablada. Lastik kısmı TPU ile ayrı da basılabilir.",
            "shapes": _wheel_shapes(),
            "placements": _one(),
        }],
        "print_tips": ["Aks deliği 3.2 milimetre: 3 milimetre çelik çubuk rahat döner.", "Daha iyi tutuş için TPU kullanın."],
    }


def template_car() -> Dict[str, Any]:
    L, W = 130.0, 60.0
    # Tekerlek ekseni yerelde +Z; X etrafında ±90° döndürünce dışa doğru (±Y) uzanır.
    # Aks merkezi: şasi ters çevrildiğinde aks deliği z = 24 - 10 = 14 mm'de kalır.
    wheel_pl = [{"pos": [x, y, 14], "rot": [-90 if y > 0 else 90, 0, 0]}
                for x in (-L / 2 + 22, L / 2 - 22) for y in (-W / 2 - 1, W / 2 + 1)]
    return {
        "name": "Model araba",
        "summary": ("130 milimetre boyunda, dört tekerlekli basit model araba: şasi, dört tekerlek ve içi boş gövde "
                    "kabuğu. Akslar 3 milimetre çelik çubuk."),
        "parts": [
            {
                "name": "Şasi",
                "purpose": "Tekerlekleri ve gövdeyi taşır.",
                "step": ("Önce şasiyi çiz: 130 milimetre boyunda, 60 milimetre eninde, 4 milimetre kalın bir plaka. "
                         "Ön ve arka akslar için alt tarafa 10 milimetre yüksek iki aks tutucu ekle, "
                         "içinden 3.4 milimetrelik aks deliği geçsin."),
                "print_orientation": "Plaka tablada, aks tutucular yukarı bakar. PLA veya PETG, yüzde 25 doluluk.",
                "shapes": [
                    _s("box", (L, W, 4), (0, 0, 2)),
                    _s("box", (12, W, 10), (-L / 2 + 22, 0, 4 + 5)),
                    _s("box", (12, W, 10), (L / 2 - 22, 0, 4 + 5)),
                    _s("cylinder", (3.4, 3.4, W + 10), (-L / 2 + 22, 0, 4 + 6), op="subtract", rot=(90, 0, 0)),
                    _s("cylinder", (3.4, 3.4, W + 10), (L / 2 - 22, 0, 4 + 6), op="subtract", rot=(90, 0, 0)),
                ],
                "placements": _one((0, 0, 24), (180, 0, 0)),
            },
            {
                "name": "Tekerlek",
                "purpose": "Aracı yuvarlar.",
                "step": _WHEEL_STEP + " Bundan dört tane basacağız.",
                "print_orientation": "Düz yüzü tablada.",
                "shapes": _wheel_shapes(),
                "placements": wheel_pl,
            },
            {
                "name": "Gövde kabuğu",
                "purpose": "Arabanın dış görünüşü; şasinin üzerine oturur.",
                "step": ("Son olarak gövdeyi çiz: 120 milimetre boyunda, 56 milimetre eninde, 30 milimetre yüksek bir kutu, "
                         "içi 2 milimetre duvar kalacak şekilde boş. Üstüne 60 milimetrelik bir kabin ekle."),
                "print_orientation": "Açık tarafı tablada, destek gerekmez.",
                "shapes": [
                    _s("box", (120, 56, 30), (0, 0, 15)),
                    _s("box", (116, 52, 30), (0, 0, 13), op="subtract"),
                    _s("box", (60, 50, 18), (-8, 0, 30 + 9)),
                    _s("box", (56, 46, 18), (-8, 0, 30 + 7), op="subtract"),
                    _s("box", (56, 46, 3), (-8, 0, 28), op="subtract"),
                ],
                "placements": _one((0, 0, 24)),
            },
        ],
        "print_tips": [
            "Akslar için 3 milimetre çelik çubuk veya M3 cıvata kullanın.",
            "Tekerlekler aksa sıkı geçmezse bir damla yapıştırıcı yeterli.",
            "Gövde kabuğu destek gerektirmez; 0.2 milimetre katman yeterli.",
        ],
    }


def template_fan_grill(size: float = 120.0) -> Dict[str, Any]:
    pitch = {40: 32.0, 80: 71.5, 92: 82.5, 120: 105.0, 140: 124.5}.get(int(size), 105.0)
    t = 3.0
    shapes = [
        _s("box", (size, size, t), (0, 0, t / 2)),
        _s("cylinder", (size - 6, size - 6, t * 3), (0, 0, t / 2), op="subtract"),
    ]
    for r in (size * 0.41, size * 0.28, size * 0.15):  # Eş merkezli halkalar (dıştan içe, sıra önemli)
        shapes.append(_s("cylinder", (2 * r + 3, 2 * r + 3, t), (0, 0, t / 2)))
        shapes.append(_s("cylinder", (2 * r - 3, 2 * r - 3, t * 3), (0, 0, t / 2), op="subtract"))
    shapes.append(_s("cylinder", (size * 0.12, size * 0.12, t), (0, 0, t / 2)))
    for a in (0, 90):
        shapes.append(_s("box", (size - 4, 3, t), (0, 0, t / 2), rot=(0, 0, a)))
    for x in (-pitch / 2, pitch / 2):
        for y in (-pitch / 2, pitch / 2):
            shapes.append(_s("cylinder", (4.5, 4.5, t * 3), (x, y, t / 2), op="subtract"))
    return {
        "name": f"{size:g} mm fan ızgarası",
        "summary": f"{size:g} milimetre bilgisayar kasa fanı için koruyucu ızgara; vida aralığı {pitch:g} milimetre.",
        "parts": [{
            "name": "Fan ızgarası",
            "purpose": "Fan pervanesini kablolardan ve parmaklardan korur.",
            "step": (f"{size:g} milimetrelik kare çerçeveyi çiz, kalınlığı 3 milimetre. İçine büyük bir daire açıp "
                     "üç eş merkezli halka ve artı şeklinde iki kol ekle. Köşelere "
                     f"{pitch:g} milimetre aralıklı 4.5 milimetrelik vida delikleri aç."),
            "print_orientation": "Düz yatar. PLA veya PETG, yüzde 30 doluluk.",
            "shapes": shapes,
            "placements": _one(),
        }],
        "print_tips": ["Fan vidaları genelde 4.5-5 milimetre; delikler buna göre açıldı.",
                       "Kasa içindeki ısı için PETG tercih edin."],
    }


def template_ssd_bracket() -> Dict[str, Any]:
    # 3.5" yuvaya 2.5" SSD adaptörü (SFF-8201 alt delikleri, SFF-8301 yan delikleri).
    L, W, t = 101.6, 101.6, 3.0
    shapes = [
        _s("box", (L, W, t), (0, 0, t / 2)),
        _s("box", (L - 16, W - 40, t * 3), (0, 0, t / 2), op="subtract"),
        _s("box", (L, 3, 12), (0, -W / 2 + 1.5, 6)),
        _s("box", (L, 3, 12), (0, W / 2 - 1.5, 6)),
    ]
    for x in (-L / 2 + 15, -L / 2 + 15 + 76.6):            # 2.5" SSD alt delikleri (61.72 mm aralık)
        for y in (-30.86, 30.86):
            shapes.append(_s("cylinder", (3.4, 3.4, t * 3), (x, y, t / 2), op="subtract"))
    for x in (-L / 2 + 28.5, -L / 2 + 28.5 + 41.28):       # 3.5" yan delikler
        for y in (-W / 2 + 1.5, W / 2 - 1.5):
            shapes.append(_s("cylinder", (3.6, 3.6, 8), (x, y, 6.35), op="subtract", rot=(90, 0, 0)))
    return {
        "name": "SSD adaptörü 2.5 - 3.5 inç",
        "summary": "2.5 inç SSD'yi bilgisayar kasasındaki 3.5 inç disk yuvasına takmak için braket.",
        "parts": [{
            "name": "SSD braketi",
            "purpose": "SSD'yi kasaya sabitler.",
            "step": ("101.6 milimetre kare taban plakasını çiz, 3 milimetre kalın; ortasını hava akışı için oy. "
                     "İki yanına 12 milimetre yüksek duvar ekle. Tabana SSD için 61.7 milimetre aralıklı M3 delikler, "
                     "yan duvarlara kasa vidaları için delikler aç."),
            "print_orientation": "Taban tablada, duvarlar yukarı. PETG, yüzde 30 doluluk.",
            "shapes": shapes,
            "placements": _one(),
        }],
        "print_tips": ["SSD M3 vidayla, braket kasaya 6-32 UNC disk vidasıyla bağlanır.",
                       "Yan delikler yatay olduğundan hafif ovalleşebilir; matkapla düzeltin."],
    }


def _num_after(text: str, pattern: str) -> Optional[float]:
    m = re.search(pattern, text)
    if not m:
        return None
    try:
        return float(m.group(1).replace(",", "."))
    except ValueError:
        return None


def match_template(text: str) -> Optional[Dict[str, Any]]:
    """Konuşmadaki nesneye uyan hazır şablonu döndürür (yoksa ``None``)."""
    low = (text or "").replace("İ", "i").replace("I", "ı").lower()
    tokens = re.findall(r"\w+", low)

    def has(*kw):
        return any(t.startswith(k) for t in tokens for k in kw)

    m = _num_after(low, r"\bm\s?(\d+(?:[.,]5)?)\b") or 3
    if has("somun"):
        return template_nut(m)
    if has("vida", "cıvata", "civata", "bulon"):
        length = _num_after(low, r"(\d+(?:[.,]\d)?)\s*(?:mm|milim)") or 12
        if length == m:
            length = 12
        return template_bolt(m, max(4.0, min(80.0, length)))
    if has("dron", "drone", "drön", "quadcopter", "kuadkopter", "multikopter"):
        return template_drone_arm() if has("kol") else template_drone()
    if has("teker", "jant", "lastik"):
        return template_wheel()
    if has("araba", "araç", "arac", "otomobil"):
        return template_car()
    if has("fan", "ızgara", "izgara", "soğutucu", "sogutucu"):
        size = _num_after(low, r"\b(40|80|92|120|140)\b") or 120
        return template_fan_grill(size)
    if has("ssd", "disk", "hdd", "harddisk"):
        return template_ssd_bracket()
    if has("bilgisayar", "kasa"):
        return template_fan_grill()
    return None


TEMPLATE_KEYWORDS = ("somun", "vida", "cıvata", "civata", "bulon", "dron", "drone", "drön", "quadcopter",
                     "kuadkopter", "multikopter", "teker", "jant", "lastik", "araba", "araç", "arac", "otomobil",
                     "fan", "ızgara", "izgara", "ssd", "hdd", "harddisk", "bilgisayar")
TEMPLATE_HINT = "vida, somun, dron, dron kolu, araba, tekerlek, fan ızgarası, SSD braketi"
