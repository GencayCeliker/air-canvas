# Changelog

All notable changes to this project are documented here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and the project uses [Semantic Versioning](https://semver.org/).

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
