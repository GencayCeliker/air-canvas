**English** | [Türkçe](README.tr.md)

# Air Canvas

[![tests](https://github.com/GencayCeliker/air-canvas/actions/workflows/tests.yml/badge.svg)](https://github.com/GencayCeliker/air-canvas/actions/workflows/tests.yml)
![Python](https://img.shields.io/badge/python-3.9%2B-blue)
[![License: MIT](https://img.shields.io/badge/license-MIT-green)](LICENSE)

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

## Project layout

```
air_canvas/
  canvas.py      Drawing engine: hand tracking, gestures, strokes, shapes, toolbar, saving
  speech.py      Turkish voice feedback (Edge TTS → gTTS → Turkish Windows voice)
  llm.py         Optional local LLM client for voice-note summaries (LM Studio / Ollama)
  config.py      Default paths, overridable with environment variables
  __main__.py    Command-line app (air-canvas)
tests/           pytest suite, synthetic input only
```

## Troubleshooting

| Problem | Fix |
| --- | --- |
| "Kamera açılamadı" (camera could not be opened) | Close other apps using the camera (Teams, Zoom, browser tabs) or try `--camera 1`. |
| `pip` cannot find `mediapipe` | Your Python version may be too new for MediaPipe. Use Python 3.10–3.12. |
| No sound, or an English accent | Install the voice extras (`pip install -e ".[voice]"`). Edge TTS and gTTS need internet; offline, add the Turkish voice in **Windows Settings → Time & language → Speech**. |
| Voice commands are not recognized | Speech recognition uses Google's free web API and needs internet and a working microphone (`PyAudio`). |
| Hand model download fails | Download [`hand_landmarker.task`](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task) manually and point `AIR_CANVAS_MODEL` to it. |
| Lines are shaky | Use even lighting and keep your hand 40–80 cm from the camera. |

## Roadmap

- [ ] Demo GIF
- [ ] English voice commands and UI
- [ ] Two-hand gestures (zoom / move the canvas)
- [ ] Offline speech recognition (faster-whisper)
- [ ] Export as SVG

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) © Gencay Çeliker
