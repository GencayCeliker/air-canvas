# Contributing / Katkıda bulunma

Thanks for your interest! / İlgin için teşekkürler! Issues and pull requests are welcome in English or Turkish.

## Development setup / Geliştirme ortamı

```bash
git clone https://github.com/GencayCeliker/air-canvas.git
cd air-canvas
python -m venv .venv
.venv\Scripts\activate          # Linux/macOS: source .venv/bin/activate
pip install -e ".[voice,dev]"
pytest
```

## Workflow / Çalışma akışı

1. Open an issue first for larger changes. / Büyük değişiklikler için önce bir issue aç.
2. Create a branch from `main`. / `main`'den yeni bir dal oluştur.
3. Keep changes focused and add a test when you fix a bug or add logic. / Değişikliği odaklı tut; hata düzeltirken veya mantık eklerken test ekle.
4. Make sure `pytest` passes, then open a pull request. / `pytest` geçtiğinden emin ol, sonra pull request aç.

`main` is protected: every change is merged through a reviewed pull request. / `main` korumalıdır: her değişiklik onaylanan bir pull request ile birleştirilir.

## Code notes / Kod notları

- All canvas changes happen on the camera-loop thread. Voice commands are queued as actions (`_actions`) and applied in the loop — do not modify `canvas` from other threads.
- Every stroke is one undo step. Shape snapping restores the pre-stroke image and draws the shape, so other drawings stay untouched.
- On-screen text is ASCII (OpenCV cannot render Turkish letters); spoken text uses correct Turkish spelling (`SPOKEN_NAMES`).
- Hardware paths (camera, microphone, speaker) must stay optional so tests run without them.
