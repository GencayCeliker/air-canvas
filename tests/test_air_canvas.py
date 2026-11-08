"""Air Canvas mantık testleri — kamera / mikrofon / MediaPipe gerektirmez."""

import math

import numpy as np
import pytest

cv2 = pytest.importorskip("cv2")

from air_canvas import canvas as ac  # noqa: E402


@pytest.fixture
def canvas(tmp_path):
    c = ac.AirCanvas(auto_init=False)
    c.voice_feedback = False
    c.save_dir = str(tmp_path)
    return c


def _circle(cx=640, cy=360, r=120, n=60, sweep=350):
    return [(int(cx + r * math.cos(math.radians(a))), int(cy + r * math.sin(math.radians(a))))
            for a in np.linspace(0, sweep, n)]


def _ellipse(cx=640, cy=360, rx=220, ry=90, n=70):
    return [(int(cx + rx * math.cos(math.radians(a))), int(cy + ry * math.sin(math.radians(a))))
            for a in np.linspace(0, 352, n)]


def _polygon(corners, per_side=12):
    pts = []
    for (x1, y1), (x2, y2) in zip(corners, corners[1:] + corners[:1]):
        for t in np.linspace(0, 1, per_side, endpoint=False):
            pts.append((int(x1 + (x2 - x1) * t), int(y1 + (y2 - y1) * t)))
    pts.append(corners[0])
    return pts


def _draw(c, points, owner="mouse"):
    c._begin_stroke(points[0], owner)
    for p in points[1:]:
        c._extend_stroke(p)
    return c._end_stroke()


# ─── Şekil tanıma ────────────────────────────────────────────────────────────

def test_classify_circle():
    shape = ac.AirCanvas._classify_stroke(_circle())
    assert shape["name"] == "DAIRE"
    assert abs(shape["radius"] - 120) < 10


def test_classify_ellipse():
    assert ac.AirCanvas._classify_stroke(_ellipse())["name"] == "ELIPS"


def test_classify_square_rectangle_triangle():
    square = _polygon([(500, 250), (700, 250), (700, 450), (500, 450)])
    rect = _polygon([(400, 250), (850, 250), (850, 450), (400, 450)])
    tri = _polygon([(640, 200), (500, 450), (780, 450)])
    assert ac.AirCanvas._classify_stroke(square)["name"] == "KARE"
    assert ac.AirCanvas._classify_stroke(rect)["name"] == "DIKDORTGEN"
    assert ac.AirCanvas._classify_stroke(tri)["name"] == "UCGEN"


def test_classify_line_and_rejects_noise():
    line = [(300 + i * 10, 300 + i * 2) for i in range(40)]
    assert ac.AirCanvas._classify_stroke(line)["name"] == "DUZ CIZGI"
    assert ac.AirCanvas._classify_stroke([(10, 200), (12, 202)]) is None          # çok kısa
    zigzag = [(300 + i * 8, 300 + (60 if i % 2 else 0)) for i in range(30)]
    assert ac.AirCanvas._classify_stroke(zigzag) is None                         # açık, düz değil


# ─── Çizgi / geçmiş ──────────────────────────────────────────────────────────

def test_freehand_stroke_is_undoable_and_redoable(canvas):
    canvas.shape_assist = False
    _draw(canvas, [(300, 300), (400, 320), (500, 300)])
    assert canvas.canvas.any()
    assert canvas._undo()
    assert not canvas.canvas.any()
    assert canvas._redo()
    assert canvas.canvas.any()


def test_new_stroke_clears_redo(canvas):
    canvas.shape_assist = False
    _draw(canvas, [(300, 300), (400, 300)])
    canvas._undo()
    _draw(canvas, [(300, 400), (400, 400)])
    assert not canvas.redo_stack


def test_undo_history_is_bounded(canvas):
    canvas.shape_assist = False
    for i in range(ac.UNDO_LIMIT + 10):
        _draw(canvas, [(200, 200 + i * 5), (300, 200 + i * 5)])
    assert len(canvas.undo_stack) == ac.UNDO_LIMIT


def test_shape_snap_keeps_other_drawings(canvas):
    canvas.shape_assist = False
    _draw(canvas, [(100, 500), (1100, 500)])  # circle'ın içinden geçen çizgi
    canvas.shape_assist = True
    canvas.color_index = 2
    assert _draw(canvas, _circle(cx=640, cy=400, r=120)) == "DAIRE"
    # Önceki çizginin dairenin kesmediği kısmı korunmalı
    assert canvas.canvas[500, 150].any()
    # Tek geri al, daireyi tamamen kaldırıp önceki çizgiyi bırakır
    canvas._undo()
    assert canvas.canvas[500, 150].any()
    assert not canvas.canvas[280, 640].any()


def test_no_ink_in_toolbar_area(canvas):
    canvas.shape_assist = False
    _draw(canvas, [(300, 50), (400, 60), (500, 50)])
    assert not canvas.canvas.any()
    assert not canvas.undo_stack


def test_hand_stroke_survives_short_detection_gap(canvas):
    canvas.shape_assist = False
    canvas._begin_stroke((300, 300), "hand")
    canvas._extend_stroke((350, 300))
    for _ in range(ac.HAND_LOST_GRACE_FRAMES - 1):
        canvas._on_hand_missing()
    assert canvas._stroke_owner == "hand"
    canvas._on_hand_missing()
    assert canvas._stroke_owner is None
    assert len(canvas.undo_stack) == 1


def test_eraser_removes_ink(canvas):
    canvas.shape_assist = False
    _draw(canvas, [(300, 300), (500, 300)])
    canvas._perform("eraser")
    _draw(canvas, [(300, 300), (500, 300)])
    assert not canvas.canvas[300, 400].any()


# ─── Görüntü birleştirme ─────────────────────────────────────────────────────

def test_compose_draws_blue_opaquely(canvas):
    """Regresyon: eski eşik (gri<50) mavi mürekkebi arka plana karıştırıyordu."""
    canvas.canvas[300:310, 300:310] = (255, 0, 0)
    frame = np.full((ac.FRAME_H, ac.FRAME_W, 3), 120, np.uint8)
    view = canvas._compose(frame)
    assert tuple(view[305, 305]) == (255, 0, 0)
    assert tuple(view[100, 100]) == (120, 120, 120)


def test_board_mode_background(canvas):
    canvas.board_mode = True
    view = canvas._compose(np.full((ac.FRAME_H, ac.FRAME_W, 3), 120, np.uint8))
    assert tuple(view[400, 400]) == ac.BOARD_BG


# ─── Menü ────────────────────────────────────────────────────────────────────

def test_toolbar_hit_testing(canvas):
    ids = []
    for b in canvas.buttons:
        ids.append(canvas._button_at((b["x1"] + b["x2"]) // 2, 50))
    assert ids == ac.TOOLBAR_IDS
    assert canvas.buttons[-1]["x2"] <= ac.FRAME_W
    assert canvas._button_at(640, 300) is None


def test_gesture_button_cooldown(canvas):
    canvas._press_button("shape", from_gesture=True)
    canvas._press_button("shape", from_gesture=True)  # bekleme süresi içinde → yok sayılır
    assert canvas.shape_assist is False


# ─── Sesli komutlar ──────────────────────────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("tuvali temizle", [("clear", None)]),
    ("geri al", [("undo", None)]),
    ("bunu kaydet", [("save", None)]),
    ("kırmızıya geç", [("color", 2)]),
    ("KIRMIZI DAİRE ÇİZ", [("color", 2), ("stamp", "DAIRE")]),
    ("silgiyi aç", [("eraser", None)]),
    ("fırçayı kalınlaştır", [("brush_up", None)]),
    ("tahta moduna geç", [("board_set", True)]),
    ("üçgen çiz", [("stamp", "UCGEN")]),
])
def test_parse_voice_command(canvas, text, expected):
    actions, message = canvas._parse_voice_command(text)
    assert actions == expected
    assert message


def test_plain_speech_is_a_note(canvas):
    actions, _ = canvas._parse_voice_command("bu proje için yarın sunum hazırlayacağım")
    assert actions == []


def test_voice_actions_applied_on_loop_thread(canvas):
    canvas._actions.put(("color", 3))
    canvas._actions.put(("stamp", "KARE"))
    assert not canvas.canvas.any()
    canvas._drain_actions()
    assert canvas.color_index == 3
    assert canvas.canvas.any()


# ─── Kaydetme ────────────────────────────────────────────────────────────────

def test_save_writes_transparent_png_and_notes(canvas, tmp_path):
    canvas.canvas[200:220, 200:220] = (0, 255, 0)
    canvas.voice_notes.append("[10:00:00] deneme notu")
    path = canvas._save()
    assert path
    img = cv2.imdecode(np.fromfile(path, np.uint8), cv2.IMREAD_UNCHANGED)
    assert img.shape[2] == 4
    assert img[210, 210, 3] == 255 and img[50, 50, 3] == 0
    notes = path.replace(".png", "_notlar.txt")
    assert "deneme notu" in open(notes, encoding="utf-8").read()
    assert canvas._save() != path  # aynı saniyede ikinci kayıt üzerine yazmaz


def test_spoken_feedback_uses_turkish_spelling_and_shared_speaker():
    class FakeSpeaker:
        def __init__(self):
            self.said = []

        def is_busy(self):
            return False

        def say(self, text, mood="neutral"):
            self.said.append(text)

    speaker = FakeSpeaker()
    c = ac.AirCanvas(auto_init=False, speaker=speaker)
    _, message = c._parse_voice_command("yeşil üçgen çiz")
    assert "yeşil" in message and "üçgen" in message
    c._say_async(message)
    assert speaker.said == [message]

    busy = FakeSpeaker()
    busy.is_busy = lambda: True
    c2 = ac.AirCanvas(auto_init=False, speaker=busy)
    c2._say_async("Kare algılandı.")
    assert busy.said == []
