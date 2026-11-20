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
- **Turkish voice commands**: say *"kırmızı daire çiz"* (draw a red circle), *"sol üste büyük mavi yıldız"* (big blue star, top left), *"kırmızı daire ve yeşil kare çiz"* (several shapes, side by side), *"silgi"* (eraser), *"tahta modu"* (whiteboard mode), *"kalınlaştır"* (thicker brush)… Shapes: circle, square, rectangle, triangle, ellipse, hexagon, star, line.
- **Voice notes**: anything that is not a command is saved as a note next to your drawing. Long notes are summarized by a local LLM (LM Studio / Ollama) if one is running.
- **Natural Turkish voice feedback** via Edge neural TTS, with gTTS and a Turkish Windows voice as fallbacks.
- **Undo / redo** (bounded history), eraser, six colors, adjustable brush, whiteboard mode.
- **Saves** a transparent PNG of the drawing, a screenshot of the full view and a text file with your voice notes.
- **Mouse fallback**: draw with the mouse, change brush size with the wheel.
- **AI 3D design assistant**: say *"dron tasarla"*, *"M3 vida çiz"* or *"model araba yap"* and get a printable design, from a screw to a full car model. It is split into parts, shown as a translucent 3D guide you can trace, explained step by step by voice, and exported as bed-ready STL files.
- **Works offline with a local model** (`--offline`): designs come from LM Studio / Ollama, speech is recognized with faster-whisper and spoken with the local Windows voice — nothing leaves your computer. The AI can ask for missing dimensions before it designs, and can ink the design onto the canvas as a line sketch.
- **Fullscreen by default** (`F` switches to a window).
- Smoothed strokes and a short grace period, so lines do not break when tracking drops a frame.

## Install

Requires Python 3.9+ and a webcam.

```bash
git clone https://github.com/GencayCeliker/air-canvas.git
cd air-canvas
pip install -e ".[voice]"      # or: pip install -e .   (no voice features)
pip install -e ".[ai]"         # optional: Claude for the 3D design assistant
pip install -e ".[offline]"    # optional: offline speech recognition (faster-whisper)
```

The MediaPipe hand model (~7.5 MB) is downloaded automatically on first start.

## Run

```bash
air-canvas                     # or: python -m air_canvas
air-canvas --board             # start on a plain whiteboard
air-canvas --camera 1 --no-voice
air-canvas --windowed          # start in a window instead of fullscreen
air-canvas --no-shape-assist   # start with shape recognition off (toggle: A)
air-canvas --design "dron"     # open with a 3D design ready
air-canvas --offline           # no internet: local LLM, Whisper and the local voice
air-canvas --ai local          # local LLM designs, but online speech is still allowed
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
| `Q` / `Esc` | Quit | `F` | Fullscreen / window |
| `N` / `P` | Next / previous design step | `R` | Repeat step |
| `G` | Show/hide 3D guide | `X` | Export design as STL |
| `K` | AI checks your drawing | `T` | Ink the design sketch onto the canvas |

## 3D design assistant

Press `V` and say what you want to build. A sentence with *tasarla*, *3D*, or an object such as *vida*, *somun*, *dron*, *araba*, *tekerlek*, *fan* or *SSD* starts a design.

1. The AI splits the object into printable parts (holes, standard sizes, 1.2 mm minimum walls, no steep overhangs) and models each part from boxes, cylinders/cones/hexagons and spheres.
   If a dimension that changes the design is missing, the AI asks first (*"Kaç inç pervane kullanacaksın?"*) and then listens for your answer. Say *"sen karar ver"* to use standard sizes or *"vazgeç"* to cancel. Requests that already contain a number are not questioned.
2. The model appears as a translucent 3D guide under your ink. The current part is highlighted, and the panel on the right shows its instructions and size in millimetres.
3. Say *"sonraki"* / *"önceki"* / *"tekrar"* to move through the parts, *"kontrol et"* to have the AI look at your drawing, or ask a question (*"duvar kalınlığı kaç olmalı?"*). *"Taslağı çiz"* (or `T`) inks the outline of the design onto the canvas so you can trace it, save it or undo it.
4. Say *"STL"* (or press `X`). Each part is written as its own STL, lying flat on the bed, together with an assembly preview, `talimatlar.txt` and `tasarim.json`, in `<save dir>/3D/`.

**Which AI is used?** With the `ai` extra and an Anthropic credential (`ANTHROPIC_API_KEY` or an `ant auth login` profile), Claude (`claude-opus-5-5`, structured JSON output) designs anything you can describe. Without Claude, a local LLM (LM Studio / Ollama) is tried. The built-in templates work offline and are the last fallback: bolt, nut, drone, drone arm, car, wheel, fan grill, SSD bracket. Choose explicitly with `--ai auto|claude|local|template`.

### Offline / local model

```bash
pip install -e ".[voice,offline]"
air-canvas --offline
```

1. Start LM Studio (`:1234`) or Ollama (`:11434`) and load a model. A *coder* model (e.g. `qwen2.5-coder` 7B/14B) is picked for designs and a *VL* model (e.g. `qwen2.5-vl`) for `kontrol et`; override with `AIR_CANVAS_LLM_DESIGN_MODEL` / `AIR_CANVAS_LLM_VISION_MODEL`.
2. The Whisper speech model (`small` by default) is downloaded **once** on first use; after that no internet is needed. To prepare a fully offline machine, run once while online or set `AIR_CANVAS_WHISPER_MODEL` to a local model folder.
3. For spoken feedback, add the Turkish voice in **Windows Settings → Time & language → Speech**.

Local models are slower than Claude: a design can take a few minutes on a CPU (`AIR_CANVAS_LLM_TIMEOUT`, default 300 s). If the model returns invalid JSON it is asked once more with the error; if it still fails, a matching built-in template is used.

## Configuration

| Environment variable | Default |
| --- | --- |
| `AIR_CANVAS_SAVE_DIR` | `~/Pictures/AirCanvas` |
| `AIR_CANVAS_MODEL` | user cache folder (`%LOCALAPPDATA%\air_canvas` / `~/.cache/air_canvas`) |
| `AIR_CANVAS_VOICE` | `tr-TR-AhmetNeural` (or `tr-TR-EmelNeural`) |
| `AIR_CANVAS_LLM_URL` | tries LM Studio `:1234` and Ollama `:11434` |
| `AIR_CANVAS_LLM_MODEL` | one model for every task (empty: picked per task) |
| `AIR_CANVAS_LLM_DESIGN_MODEL` | model for 3D designs (JSON); default: a model with "coder" in its name |
| `AIR_CANVAS_LLM_VISION_MODEL` | model for drawing checks (images); default: a model with "vl"/"vision" in its name |
| `ANTHROPIC_API_KEY` | enables Claude for the 3D design assistant |
| `AIR_CANVAS_CLAUDE_MODEL` | `claude-opus-5-5` |
| `AIR_CANVAS_AI` | `auto` (Claude → local LLM → templates), `claude`, `local` or `template` |
| `AIR_CANVAS_OFFLINE` | `1` = same as `--offline` |
| `AIR_CANVAS_STT` | `auto` (Google, then Whisper), `google` or `whisper` |
| `AIR_CANVAS_WHISPER_MODEL` | `small` (or `tiny`/`base`/`medium`, or a local folder) |
| `AIR_CANVAS_LLM_TIMEOUT` | `300` seconds for a local design |
| `AIR_CANVAS_CLARIFY` | `1`; `0` = never ask for dimensions before designing |

## Use as a library

```python
from air_canvas import AirCanvas

canvas = AirCanvas(save_dir="drawings")
canvas.start_async(on_finish=print)   # non-blocking; or canvas.run() to block
```

You can plug in your own voice engine (`speaker=` — any object with `say(text)` and `is_busy()`, optionally `stop()`), speech recognizer (`listener=` — `listen()` returning text), LLM client (`llm=`) and design assistant (`assistant=`). `AirCanvas(offline=True)` and `ai_mode=` match the command-line flags.

## How shape recognition works

When a stroke ends, its points are simplified with `approxPolyDP` and analysed on their convex hull: the ratio of start–end distance to path length separates lines from closed shapes; vertex count, circularity (`4πA/P²`) and extent (area ÷ bounding-box area) separate triangles, rectangles and round shapes; `minAreaRect` and `fitEllipse` give orientation and size. Strokes that are not convincingly closed are left as drawn.

## Tests

```bash
pip install -e ".[dev]"
pytest
```

The suite covers shape classification, undo/redo, compositing, toolbar hit-testing, gesture timing, voice-command parsing, the speech queue, listening errors, local-model designs and saving — all with synthetic input and fake engines, no camera, microphone or network needed.

## Project layout

```
air_canvas/
  canvas.py      Drawing engine: hand tracking, gestures, strokes, shapes, toolbar, saving
  speech.py      Turkish voice feedback (Edge TTS → gTTS → Turkish Windows voice), interruptible
  listen.py      Speech recognition: Google online, faster-whisper offline, clear error messages
  llm.py         Optional local LLM client (LM Studio / Ollama): summaries, designs, questions, drawing checks
  textutil.py    Cleans model output for speech (reasoning blocks, markdown, emoji)
  design.py      3D designs: parts, solid modelling (manifold3d), STL export, 3D preview, templates
  ai.py          Design assistant: Claude, then a local LLM, then built-in templates
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
| Voice commands are not recognized | The assistant now says why: no microphone/`PyAudio`, no internet, or speech not understood. Online recognition uses Google's free web API; for offline use install the `offline` extra (faster-whisper). |
| The local model is slow or produces nothing | Use a smaller *coder* model, raise `AIR_CANVAS_LLM_TIMEOUT`, and check the LM Studio / Ollama server log. Run with `-v` to see which model was picked. |
| Hand model download fails | Download [`hand_landmarker.task`](https://storage.googleapis.com/mediapipe-models/hand_landmarker/hand_landmarker/float16/1/hand_landmarker.task) manually and point `AIR_CANVAS_MODEL` to it. |
| Lines are shaky | Use even lighting and keep your hand 40–80 cm from the camera. |

## Roadmap

- [ ] Demo GIF
- [ ] English voice commands and UI
- [ ] Two-hand gestures (zoom / move the canvas)
- [x] Offline speech recognition (faster-whisper)
- [x] Local model designs and offline mode
- [ ] Export as SVG

## Contributing

Issues and pull requests are welcome — see [CONTRIBUTING.md](CONTRIBUTING.md). Changes are listed in [CHANGELOG.md](CHANGELOG.md).

## License

[MIT](LICENSE) © Gencay Çeliker
