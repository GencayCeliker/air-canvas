# Air Canvas

**Draw in the air with your finger.** Air Canvas tracks your hand through an ordinary webcam, turns your index finger into a brush, cleans up rough circles, rectangles and triangles into perfect shapes, and understands Turkish voice commands.

<!-- TODO: add demo GIF, e.g. docs/demo.gif -->

> The on-screen interface, voice commands and spoken feedback are in **Turkish**. Air Canvas started as a module of my desktop assistant project Moly/Jarvis and now also runs as a standalone app.

## Features

- **Hand-gesture drawing** with MediaPipe hand tracking — no special hardware, just a webcam.
- **Shape recognition**: rough strokes snap to a clean line, triangle, square, rectangle, circle or ellipse (rotation-aware). Snapping only replaces your stroke, never other drawings.
- **Turkish voice commands**: say *"kırmızı daire çiz"* (draw a red circle), *"silgi"* (eraser), *"tahta modu"* (whiteboard mode), *"kalınlaştır"* (thicker brush)…
- **Voice notes**: anything that is not a command is saved as a note next to your drawing. Long notes are summarized by a local LLM (LM Studio / Ollama) if one is running.
- **Natural Turkish voice feedback** via Edge neural TTS, with gTTS and a Turkish Windows voice as fallbacks.
- **Undo / redo** (bounded history), eraser, six colors, adjustable brush, whiteboard mode.
- **Saves** a transparent PNG of the drawing, a screenshot of the full view and a text file with your voice notes.
- **Mouse fallback**: draw with the mouse, change brush size with the wheel.
- Smoothed strokes and a short grace period, so lines do not break when tracking drops a frame.

## Install

Requires Python 3.9+ and a webcam.

```bash
git clone https://github.com/GencayCeliker/air-canvas.git
cd air-canvas
pip install -e ".[voice]"      # or: pip install -e .   (no voice features)
```

The MediaPipe hand model (~7.5 MB) is downloaded automatically on first start.

## Run

```bash
air-canvas                     # or: python -m air_canvas
air-canvas --board             # start on a plain whiteboard
air-canvas --camera 1 --no-voice
air-canvas --help
```

## Controls

| Gesture | Action |
| --- | --- |
| ☝️ Index finger up | Draw |
| ✌️ Index + middle finger | Select — hover over a toolbar button to press it |
| 🖐️ Four fingers up | Brush size — pinch thumb and index finger closer or wider |

| Key | Action | Key | Action |
| --- | --- | --- | --- |
| `1`–`6` | Color | `E` | Eraser / pen |
| `Z` / `Y` | Undo / redo | `C` | Clear |
| `S` | Save | `V` | Voice command / note |
| `B` | Whiteboard mode | `A` | Shape recognition on/off |
| `+` / `-` | Brush size | `H` | Show/hide help panel |
| `Q` / `Esc` | Quit | | |

## Configuration

| Environment variable | Default |
| --- | --- |
| `AIR_CANVAS_SAVE_DIR` | `~/Pictures/AirCanvas` |
| `AIR_CANVAS_MODEL` | user cache folder (`%LOCALAPPDATA%\air_canvas` / `~/.cache/air_canvas`) |
| `AIR_CANVAS_VOICE` | `tr-TR-AhmetNeural` (or `tr-TR-EmelNeural`) |
| `AIR_CANVAS_LLM_URL` | tries LM Studio `:1234` and Ollama `:11434` |
| `AIR_CANVAS_LLM_MODEL` | first model the server reports |

## Use as a library

```python
from air_canvas import AirCanvas

canvas = AirCanvas(save_dir="drawings")
canvas.start_async(on_finish=print)   # non-blocking; or canvas.run() to block
```

You can plug in your own voice engine (`speaker=` — any object with `say(text)` and `is_busy()`) and LLM client (`llm=`).

## How shape recognition works

When a stroke ends, its points are simplified with `approxPolyDP` and analysed on their convex hull: the ratio of start–end distance to path length separates lines from closed shapes; vertex count, circularity (`4πA/P²`) and extent (area ÷ bounding-box area) separate triangles, rectangles and round shapes; `minAreaRect` and `fitEllipse` give orientation and size. Strokes that are not convincingly closed are left as drawn.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The suite covers shape classification, undo/redo, compositing, toolbar hit-testing, gesture timing, voice-command parsing and saving — all with synthetic input, no camera needed.

## License

[MIT](LICENSE) © Gencay Çeliker
