"""air_canvas.project — Proje klasörü: ölçüler, fotoğraflar ve hazır parçalar.

Üzerinde çalışılan nesne için ``<kayıt klasörü>/projeler/<ad>/`` açılır::

    bilgi.txt     Ölçüler ve notlar ("pervane: 5 inç", "motor: 2207", "kol: 110 mm" ...)
    foto/         Referans fotoğraflar (yapıyı anlamak için görsel modele gösterilir)
    referans/     Hazır STL parçalar (rehberde gösterilir, STL dışa aktarımına eklenir)

Ölçüler fotoğraftan değil ``bilgi.txt``'den alınır: fotoğraftan milimetre güvenilir çıkmaz.
"""

import logging
import os
import re
import struct
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

import numpy as np

log = logging.getLogger(__name__)

PHOTO_EXTS = (".jpg", ".jpeg", ".png", ".webp", ".bmp")
MAX_PHOTOS = 4
MAX_REFERENCE_TRIANGLES = 80_000   # Önizleme her üçgeni ayrı çizer; çok büyük ağlar donmaya yol açar

INFO_TEMPLATE = """# Air Canvas proje bilgisi. '#' ile başlayan satırlar yok sayılır.
# "anahtar: değer" satırları şablonlara ölçü olarak aktarılır; diğer her şey tasarım
# yapan yapay zekâya not olarak verilir. Örnekler (başındaki # işaretini silin):
#
# pervane: 5 inç
# kanat: 3
# motor: 2207
# motor delik: 16 mm
# kol: 110 mm
# fc: 30.5 mm
# vida: M3x12
#
# Fotoğrafları foto/ klasörüne, hazır STL parçaları referans/ klasörüne koyun.
"""


@dataclass
class Project:
    name: str
    folder: str
    info: str = ""                                   # Yorumlar çıkarılmış bilgi.txt
    params: Dict[str, Any] = field(default_factory=dict)
    photos: List[str] = field(default_factory=list)
    references: List[Tuple[str, np.ndarray, np.ndarray]] = field(default_factory=list)
    skipped: List[str] = field(default_factory=list)  # Yüklenemeyen dosyalar ve nedeni
    photo_summary: str = ""

    def status_text(self) -> str:
        return f"{self.name} ({len(self.photos)} foto, {len(self.references)} STL, {len(self.params)} olcu)"

    def context_text(self) -> str:
        """Tasarım isteğine eklenecek proje bağlamı (boşsa "")."""
        lines = []
        if self.info:
            lines.append(f"Proje '{self.name}' notlari ve olculeri (bu olculere uy):\n{self.info}")
        if self.photo_summary:
            lines.append(f"Referans fotograflardan cikan yapi:\n{self.photo_summary}")
        if self.references:
            names = ", ".join(n for n, _, _ in self.references)
            lines.append(f"Projede hazir parcalar var (bunlari tekrar tasarlama, uyumlu yap): {names}")
        return "\n\n".join(lines)


def projects_root(save_dir: str) -> str:
    return os.path.join(save_dir, "projeler")


def _tr_lower(text: str) -> str:
    return (text or "").replace("İ", "i").replace("I", "ı").lower()


def project_slug(name: str) -> str:
    from .design import slugify
    return slugify(name) or "proje"


def _motor_spacing_from_size(size: str) -> Optional[float]:
    """Stator çapına göre yaygın delik aralığı: 2207/2806 → 16, 2004 → 12, 1404 → 9 mm."""
    m = re.match(r"(\d{2})\d{2}$", size)
    if not m:
        return None
    stator = int(m.group(1))
    return 16.0 if stator >= 22 else 12.0 if stator >= 18 else 9.0


def parse_params(text: str) -> Dict[str, Any]:
    """``bilgi.txt`` satırlarından şablon ölçülerini çıkarır."""
    params: Dict[str, Any] = {}
    for raw in (text or "").splitlines():
        line = raw.split("#", 1)[0].strip()
        if not re.search(r"[:=]", line):
            continue
        key, value = re.split(r"[:=]", line, maxsplit=1)
        key, value = _tr_lower(key).strip(), _tr_lower(value).strip()
        nums = [float(n.replace(",", ".")) for n in re.findall(r"\d+(?:[.,]\d+)?", value)]
        if not nums:
            continue
        mm = nums[0] * (10.0 if re.search(r"\bcm\b", value) else 1.0)
        if key.startswith(("pervane", "prop")):
            params["prop_inch"] = nums[0]
        elif key.startswith("kanat"):
            params["blades"] = int(nums[0])
        elif key.startswith("motor") and re.search(r"delik|aral", key):
            params["motor_spacing"] = mm
        elif key.startswith("motor"):
            size = re.search(r"\b(\d{4})\b", value)
            if size:
                params["motor_size"] = size.group(1)
                spacing = _motor_spacing_from_size(size.group(1))
                if spacing:
                    params.setdefault("motor_spacing", spacing)
        elif key.startswith("kol"):
            params["arm_length"] = mm
        elif key.startswith(("fc", "uçuş", "ucus")):
            params["fc_spacing"] = mm
        elif key.startswith(("vida", "cıvata", "civata", "bulon")):
            bolt = re.search(r"m\s?(\d+(?:[.,]5)?)(?:\s*[x×*]\s*(\d+(?:[.,]\d+)?))?", value)
            if bolt:
                params["bolt_m"] = float(bolt.group(1).replace(",", "."))
                if bolt.group(2):
                    params["bolt_length"] = float(bolt.group(2).replace(",", "."))
    return params


# ─── STL okuma ────────────────────────────────────────────────────────────────

def read_stl(path: str) -> Tuple[np.ndarray, np.ndarray]:
    """İkili veya ASCII STL'yi okur; aynı köşeleri birleştirip (köşeler, üçgenler) döndürür."""
    with open(path, "rb") as fh:
        data = fh.read()
    raw: np.ndarray
    if len(data) >= 84:
        count = struct.unpack("<I", data[80:84])[0]
        if 84 + 50 * count == len(data):
            rec = np.frombuffer(data, dtype=np.dtype([("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")]),
                                count=count, offset=84)
            raw = rec["v"].astype(np.float64).reshape(-1, 3)
        else:
            raw = _ascii_vertices(data)
    else:
        raw = _ascii_vertices(data)
    if len(raw) == 0 or len(raw) % 3:
        raise ValueError("STL içinde üçgen bulunamadı")
    verts, inverse = np.unique(np.round(raw, 4), axis=0, return_inverse=True)
    tris = inverse.reshape(-1, 3).astype(np.int64)
    keep = (tris[:, 0] != tris[:, 1]) & (tris[:, 1] != tris[:, 2]) & (tris[:, 0] != tris[:, 2])
    return verts, tris[keep]


def _ascii_vertices(data: bytes) -> np.ndarray:
    text = data.decode("utf-8", "replace")
    if "facet" not in text:
        raise ValueError("STL biçimi tanınmadı")
    found = re.findall(r"vertex\s+(\S+)\s+(\S+)\s+(\S+)", text)
    return np.array(found, dtype=np.float64).reshape(-1, 3)


def is_watertight(tris: np.ndarray) -> bool:
    """Her kenar tam iki üçgende kullanılıyorsa ağ kapalıdır (dilimleyicide sorunsuz açılır)."""
    if not len(tris):
        return False
    edges = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
    _, counts = np.unique(edges, axis=0, return_counts=True)
    return bool((counts == 2).all())


# ─── Proje açma ───────────────────────────────────────────────────────────────

def open_project(save_dir: str, name: str, create: bool = True) -> Project:
    """Proje klasörünü açar (yoksa şablon ``bilgi.txt`` ile oluşturur) ve içeriğini yükler."""
    label = (name or "").strip() or "proje"
    folder = os.path.join(projects_root(save_dir), project_slug(label))
    info_path = os.path.join(folder, "bilgi.txt")
    if create:
        os.makedirs(os.path.join(folder, "foto"), exist_ok=True)
        os.makedirs(os.path.join(folder, "referans"), exist_ok=True)
        if not os.path.exists(info_path):
            with open(info_path, "w", encoding="utf-8") as fh:
                fh.write(INFO_TEMPLATE)
    project = Project(name=label, folder=folder)

    if os.path.exists(info_path):
        with open(info_path, encoding="utf-8", errors="replace") as fh:
            text = fh.read()
        project.info = "\n".join(line.rstrip() for line in text.splitlines()
                                 if line.strip() and not line.lstrip().startswith("#"))[:3000]
        project.params = parse_params(text)

    photo_dir = os.path.join(folder, "foto")
    if os.path.isdir(photo_dir):
        project.photos = sorted(os.path.join(photo_dir, f) for f in os.listdir(photo_dir)
                                if f.lower().endswith(PHOTO_EXTS))

    ref_dir = os.path.join(folder, "referans")
    if os.path.isdir(ref_dir):
        for fname in sorted(os.listdir(ref_dir)):
            if not fname.lower().endswith(".stl"):
                continue
            try:
                verts, tris = read_stl(os.path.join(ref_dir, fname))
            except (OSError, ValueError) as exc:
                project.skipped.append(f"{fname}: okunamadı ({exc})")
                continue
            if len(tris) > MAX_REFERENCE_TRIANGLES:
                project.skipped.append(f"{fname}: çok büyük ({len(tris)} üçgen, en fazla {MAX_REFERENCE_TRIANGLES})")
            elif not is_watertight(tris):
                project.skipped.append(f"{fname}: su geçirmez değil (açık kenarlar var)")
            else:
                project.references.append((os.path.splitext(fname)[0], verts, tris))
    for note in project.skipped:
        log.warning("[Proje] %s", note)
    return project


def photo_mosaic(paths: List[str], tile: int = 512) -> Optional[bytes]:
    """En fazla ``MAX_PHOTOS`` fotoğrafı tek bir PNG'de birleştirir (görsel modele tek resim gider)."""
    import cv2

    tiles = []
    for path in paths[:MAX_PHOTOS]:
        img = cv2.imdecode(np.fromfile(path, dtype=np.uint8), cv2.IMREAD_COLOR)
        if img is None:
            continue
        h, w = img.shape[:2]
        scale = tile / max(h, w)
        img = cv2.resize(img, (max(1, int(w * scale)), max(1, int(h * scale))))
        canvas = np.full((tile, tile, 3), 255, np.uint8)
        y, x = (tile - img.shape[0]) // 2, (tile - img.shape[1]) // 2
        canvas[y:y + img.shape[0], x:x + img.shape[1]] = img
        tiles.append(canvas)
    if not tiles:
        return None
    if len(tiles) == 1:
        mosaic = tiles[0]
    else:
        while len(tiles) % 2:
            tiles.append(np.full_like(tiles[0], 255))
        rows = [np.hstack(tiles[i:i + 2]) for i in range(0, len(tiles), 2)]
        mosaic = np.vstack(rows)
    ok, buf = cv2.imencode(".png", mosaic)
    return buf.tobytes() if ok else None
