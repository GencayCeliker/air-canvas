# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

## [Unreleased]

### Added
- AI 3D design assistant: voice requests such as *"dron tasarla"* produce a printable multi-part design. Claude (structured outputs) is used first, then a local LLM, then built-in templates for bolt, nut, drone, drone arm, car, wheel, fan grill and SSD bracket.
- Translucent shaded 3D guide under the user's ink, a step panel with sizes, and spoken step-by-step instructions (`sonraki`, `önceki`, `tekrar`, `genel`).
- Watertight solid modelling with manifold3d and STL export: one bed-ready file per part, an assembly preview, instructions and the design as JSON.
- The AI answers spoken questions and reviews the current drawing (`kontrol et`).
- Fullscreen window by default. `F` toggles it, `--windowed` starts in a window, `--design` opens with a design.
- Offline mode (`--offline`, `AIR_CANVAS_OFFLINE`): local LLM designs, offline speech recognition with faster-whisper (`offline` extra) and the local Windows voice; no data leaves the computer (#13).
- `--ai auto|claude|local|template` (`AIR_CANVAS_AI`) chooses which AI designs.
- Before designing, the AI can ask one question about a missing key dimension and listens for the answer; *"sen karar ver"* uses standard sizes, *"vazgeç"* cancels (#13).
- *"Taslağı çiz"* / `T` inks the design outline onto the canvas as an undoable line sketch (#13).
- Local-model designs use the per-task model, a JSON schema, an example and a retry with the error message; drawing checks fall back to a local vision model (#18).
- Voice shapes: rectangle, ellipse, hexagon, star and line; size (*büyük*, *küçük*) and position (*sola*, *sağ üste* …); several shapes in one sentence, each in its own color (#17).
- Long AI answers are shown in a wrapped box above the help panel.
- Local model replies are streamed. The HUD shows tokens and speed and warns when the model is slow (running on the CPU). `AIR_CANVAS_LLM_TIMEOUT` is now the time the server may stay silent, default 180 s (#20).
- Whisper gets the command words as a hint. With the `small` model, exact recognition of 20 short commands went from 9 to 14, and most remaining differences are only punctuation (#21).

### Fixed
- Queued spoken replies (design summary, steps, AI answers) were dropped when they arrived while the assistant was speaking (#14).
- The microphone picked up the assistant's own voice; speech is now interrupted when listening starts (#14).
- Reasoning blocks (`<think>`), markdown and emoji from model answers were read aloud (#15).
- Every listening failure said "Sesinizi duyamadım"; missing packages, no microphone, no internet and unclear speech now get their own message (#16).
- Questions and drawing checks sent while the AI was busy were silently dropped (#16).
- *"Dikdörtgen"* drew a square (#17).
- The design order did not match the docs (templates came before the local LLM) and local designs timed out after 20 s (#18).
- Local designs were very slow: chat requests used the first model in the list (a VL model), so two 7B models were loaded and GPU memory overflowed. The model then ran on the CPU at 6 tokens/s and *"dron tasarla"* timed out after 300 s. Model choice now prefers models that are already loaded, and chat no longer pulls in a second model; the same design takes about 5 s (#20).
- Offline mode had no voice when the Turkish Windows voice (*Microsoft Tolga*) was only installed as a OneCore voice (#21).
- `--offline` still contacted huggingface.co on every start; Whisper now loads from the local cache first (#21).
- A comma from Whisper (*"Yeşil, üçgen …"*) split the color from its shape. *"sonraki adım"* or *"taslağı çiz"* without an open design was saved as a voice note instead of explaining what to do (#21).
- Without `ANTHROPIC_API_KEY`, `auto` mode crashed the design instead of falling back to the local LLM or templates, because the SDK raises `TypeError` (#19).
- `-v` flooded the log with comtypes/matplotlib debug lines; it now enables debug logs for Air Canvas only (#19).
- `pyproject.toml` uses an SPDX license string (setuptools deprecation warning); the README explains the `click` conflict between gTTS and newer huggingface-hub (#19).
- OpenCV 5 compatibility (mouse wheel), mediapipe API access and type-narrowing fixes (#3–#10).
- Running `air_canvas/__main__.py` directly no longer fails with a relative-import error.
- Questions such as *"duvar kalınlığı kaç olmalı?"* are no longer mistaken for the *kalın* brush command.

## [1.0.0] — 2026-11-08

First standalone release, extracted from the Moly/Jarvis desktop assistant.

### Added
- Hand-gesture drawing with MediaPipe HandLandmarker (VIDEO mode, frame-to-frame tracking).
- Gestures: index finger draws, index + middle selects toolbar buttons, four fingers adjust brush size.
- Shape recognition for lines, triangles, squares, rectangles, circles and ellipses, rotation-aware.
- Turkish voice commands, including combined commands such as *"kırmızı daire çiz"*.
- Voice notes saved next to drawings; optional summaries by a local LLM (LM Studio / Ollama).
- Turkish voice feedback: Edge neural TTS → gTTS → Turkish Windows voice (silent otherwise).
- Undo / redo with bounded history, eraser, six colors, whiteboard mode, mouse fallback.
- Saving as transparent PNG + full-view screenshot + notes file.
- `air-canvas` command-line app and a library API (`AirCanvas`, pluggable `speaker` and `llm`).
- Test suite (28 tests) running without a camera; CI on Windows and Linux.
- English and Turkish documentation.
