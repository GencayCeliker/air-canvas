"""Komut satırı: ``air-canvas`` veya ``python -m air_canvas``."""

import argparse
import logging
import sys

if __package__ in (None, ""):
    # Dosya doğrudan çalıştırıldı (ör. ``python air_canvas/__main__.py`` ya da
    # IDE'de "Run Python File"); göreli import'lar için paket kökünü ekle.
    import os
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from air_canvas import __version__
from air_canvas.canvas import AirCanvas


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(
        prog="air-canvas",
        description="Draw in the air with your finger (webcam + MediaPipe). Turkish voice commands and feedback.",
    )
    parser.add_argument("--camera", type=int, default=0, help="camera index (default: 0)")
    parser.add_argument("--save-dir", help="folder for saved drawings (default: ~/Pictures/AirCanvas)")
    parser.add_argument("--board", action="store_true", help="start in whiteboard mode instead of camera view")
    parser.add_argument("--no-voice", action="store_true", help="disable spoken feedback")
    parser.add_argument("--no-shape-assist", action="store_true", help="start with shape recognition off")
    parser.add_argument("-v", "--verbose", action="store_true", help="show debug logs")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    args = parser.parse_args(argv)

    logging.basicConfig(level=logging.DEBUG if args.verbose else logging.INFO,
                        format="%(levelname)s %(message)s")

    canvas = AirCanvas(save_dir=args.save_dir, camera_index=args.camera)
    canvas.board_mode = args.board
    canvas.voice_feedback = not args.no_voice
    canvas.shape_assist = not args.no_shape_assist
    if not canvas.available:
        print(canvas._unavailable_message(), file=sys.stderr)
        return 1

    print(canvas.run())
    return 0


if __name__ == "__main__":
    sys.exit(main())
