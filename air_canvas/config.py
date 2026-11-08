"""Varsayılan dosya yolları. Ortam değişkenleriyle değiştirilebilir:

AIR_CANVAS_SAVE_DIR   Çizimlerin kaydedileceği klasör (varsayılan: ~/Pictures/AirCanvas)
AIR_CANVAS_MODEL      MediaPipe hand_landmarker.task dosyasının yolu
                      (varsayılan: kullanıcı önbellek klasörü; yoksa otomatik indirilir)
"""

import os


def default_save_dir() -> str:
    env = os.getenv("AIR_CANVAS_SAVE_DIR")
    if env:
        return env
    return os.path.join(os.path.expanduser("~"), "Pictures", "AirCanvas")


def _cache_dir() -> str:
    if os.name == "nt":
        base = os.getenv("LOCALAPPDATA") or os.path.join(os.path.expanduser("~"), "AppData", "Local")
    else:
        base = os.getenv("XDG_CACHE_HOME") or os.path.join(os.path.expanduser("~"), ".cache")
    return os.path.join(base, "air_canvas")


def default_model_path() -> str:
    return os.getenv("AIR_CANVAS_MODEL") or os.path.join(_cache_dir(), "models", "hand_landmarker.task")
