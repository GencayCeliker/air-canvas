"""3D tasarım asistanı testleri — kamera, mikrofon veya ağ gerektirmez."""

import json
import os
import struct
import time
from types import SimpleNamespace

import numpy as np
import pytest

pytest.importorskip("manifold3d")
cv2 = pytest.importorskip("cv2")

from air_canvas import canvas as ac  # noqa: E402
from air_canvas import design as dsg  # noqa: E402
from air_canvas.ai import DesignAssistant  # noqa: E402


# ─── Şablonlar ve modelleme ──────────────────────────────────────────────────

@pytest.mark.parametrize("request_text", [
    "M4 vida 20 mm", "somun", "dron tasarla", "dron kolu", "araba", "tekerlek",
    "120 fan ızgarası", "SSD braketi", "bilgisayar parçası",
])
def test_every_template_builds_watertight_parts(request_text):
    data = dsg.match_template(request_text)
    assert data is not None
    design = dsg.Design(data)
    assert not design.failed_parts
    for verts, tris in design.meshes:
        assert len(tris) > 0
        # Su geçirmez ağ: her kenar tam olarak iki üçgende kullanılır.
        edges = np.sort(np.concatenate([tris[:, [0, 1]], tris[:, [1, 2]], tris[:, [2, 0]]]), axis=1)
        _, counts = np.unique(edges, axis=0, return_counts=True)
        assert (counts == 2).all()


def test_bolt_template_reads_size_from_speech():
    design = dsg.Design(dsg.match_template("M4 vida 20 mm"))
    x, y, z = design.part_size(0)
    assert design.parts[0]["name"].startswith("M4")
    assert z == pytest.approx(0.7 * 4 + 20, abs=0.1)          # baş + gövde


def test_unknown_object_has_no_template():
    assert dsg.match_template("bana bir uzay istasyonu tasarla") is None


def test_shapes_apply_in_order():
    """Boşluk açıldıktan sonra eklenen şekil korunmalı (ızgara halkaları gibi)."""
    part = {"shapes": [
        dsg._s("box", (20, 20, 2), (0, 0, 1)),
        dsg._s("cylinder", (16, 16, 6), (0, 0, 1), op="subtract"),
        dsg._s("cylinder", (6, 6, 2), (0, 0, 1)),
    ]}
    solid = dsg.build_part(dsg.normalize_design({"parts": [{**part, "name": "x"}]})["parts"][0])
    plate = 20 * 20 * 2 - np.pi * 8 ** 2 * 2
    hub = np.pi * 3 ** 2 * 2
    assert solid.volume() == pytest.approx(plate + hub, rel=0.02)


def test_normalize_clamps_and_drops_invalid_input():
    raw = {
        "name": "t", "summary": "", "print_tips": [],
        "parts": [
            {"name": "ok", "shapes": [{"kind": "box", "op": "add", "size": [1e9, -5, "x"],
                                       "pos": [1e9, 0], "rot": [0, 0, 0], "segments": 2}]},
            {"name": "only holes", "shapes": [{"kind": "box", "op": "subtract", "size": [1, 1, 1],
                                               "pos": [0, 0, 0], "rot": [0, 0, 0], "segments": 0}]},
            {"name": "bad kind", "shapes": [{"kind": "torus", "op": "add"}]},
        ],
    }
    norm = dsg.normalize_design(raw)
    assert [p["name"] for p in norm["parts"]] == ["ok"]
    shape = norm["parts"][0]["shapes"][0]
    assert shape["size"][0] == dsg.MAX_DIM and shape["size"][1] == dsg.MIN_DIM
    assert shape["pos"] == [dsg.MAX_POS, 0.0, 0.0]
    assert shape["segments"] == 0
    assert norm["parts"][0]["placements"] == [{"pos": [0.0, 0.0, 0.0], "rot": [0.0, 0.0, 0.0]}]
    assert dsg.normalize_design({"parts": []}) is None
    assert dsg.normalize_design("not a dict") is None


def test_export_writes_bed_ready_stl_files(tmp_path):
    design = dsg.Design(dsg.match_template("araba"))
    folder = dsg.export_design(design, str(tmp_path))
    files = sorted(os.listdir(folder))
    assert "talimatlar.txt" in files and "tasarim.json" in files and "montaj_onizleme.stl" in files
    wheel = next(f for f in files if "tekerlek" in f)
    assert wheel.endswith("_x4.stl")

    with open(os.path.join(folder, wheel), "rb") as fh:
        header = fh.read(80)
        (count,) = struct.unpack("<I", fh.read(4))
        body = fh.read()
    assert not header.startswith(b"solid")
    assert len(body) == count * 50
    rec = np.frombuffer(body, dtype=[("n", "<f4", 3), ("v", "<f4", (3, 3)), ("a", "<u2")])
    assert rec["v"][..., 2].min() == pytest.approx(0.0, abs=1e-4)   # tablaya oturuyor
    assert "Şasi" in open(os.path.join(folder, "talimatlar.txt"), encoding="utf-8").read()


def test_preview_highlights_current_part():
    design = dsg.Design(dsg.match_template("araba"))
    img = dsg.render_preview(design, 400, 300, highlight=1)
    assert img.shape == (400 * 0 + 300, 400, 3) and img.any()
    highlight = np.all(np.abs(img.astype(int) - dsg.HIGHLIGHT) < 120, axis=2) & (img[..., 2] > img[..., 0])
    assert highlight.any()


# ─── AI asistanı (sahte Claude istemcisiyle) ─────────────────────────────────

class FakeClaude:
    def __init__(self, text, stop_reason="end_turn"):
        self.calls = []
        self._text = text
        self._stop = stop_reason
        self.beta = SimpleNamespace(messages=SimpleNamespace(create=self._create))

    def _create(self, **kwargs):
        self.calls.append(kwargs)
        return SimpleNamespace(stop_reason=self._stop, stop_details=None,
                               content=[SimpleNamespace(type="text", text=self._text)])


def _claude_design_json():
    return json.dumps({
        "name": "Kalemlik", "summary": "Basit kalemlik.", "print_tips": ["PLA yeterli."],
        "parts": [{
            "name": "Gövde", "purpose": "Kalemleri tutar.", "step": "Bir silindir çiz.",
            "print_orientation": "Dik.", "placements": [{"pos": [0, 0, 0], "rot": [0, 0, 0]}],
            "shapes": [
                {"kind": "cylinder", "op": "add", "size": [60, 60, 90], "pos": [0, 0, 45], "rot": [0, 0, 0], "segments": 0},
                {"kind": "cylinder", "op": "subtract", "size": [56, 56, 90], "pos": [0, 0, 47], "rot": [0, 0, 0], "segments": 0},
            ],
        }],
    })


def test_claude_design_request_uses_structured_output_and_fallbacks():
    pytest.importorskip("anthropic")
    fake = FakeClaude(_claude_design_json())
    design, source = DesignAssistant(client=fake).design("kalemlik tasarla")
    assert source == "Claude" and design.name == "Kalemlik"
    call = fake.calls[0]
    assert call["model"] == "claude-opus-5-5"
    assert call["fallbacks"] == "default" and call["betas"] == ["server-side-fallback-2026-07-01"]
    assert call["output_config"]["format"]["type"] == "json_schema"
    assert "thinking" not in call and "temperature" not in call


def test_claude_refusal_falls_back_to_template():
    pytest.importorskip("anthropic")
    fake = FakeClaude("", stop_reason="refusal")
    design, source = DesignAssistant(client=fake).design("araba tasarla")
    assert source == "sablon" and design.name == "Model araba"


def test_without_any_ai_unknown_object_fails_cleanly():
    design, source = DesignAssistant(use_claude=False).design("uzay istasyonu")
    assert design is None and source == ""


def test_ask_falls_back_to_local_llm():
    local = SimpleNamespace(available=True, generate=lambda *a, **k: "Duvarı 2 milimetre yap.")
    assistant = DesignAssistant(local_llm=lambda: local, use_claude=False)
    assert assistant.ask("duvar ne kadar olmalı") == "Duvarı 2 milimetre yap."


# ─── Tuval entegrasyonu ──────────────────────────────────────────────────────

@pytest.fixture
def canvas(tmp_path):
    c = ac.AirCanvas(auto_init=False, assistant=DesignAssistant(use_claude=False))
    c.voice_feedback = False
    c.save_dir = str(tmp_path)
    return c


@pytest.mark.parametrize("text, action", [
    ("bir dron tasarla", "design_request"),
    ("M3 vida çiz", "design_request"),
    ("model araba yap", "design_request"),
    ("bunu stl olarak kaydet", "design_export"),
    ("daire çiz", "stamp"),
    ("kaydet", "save"),
])
def test_voice_routes_design_commands(canvas, text, action):
    actions, _ = canvas._parse_voice_command(text)
    assert actions[0][0] == action


def test_question_goes_to_ai_and_plain_speech_stays_a_note(canvas):
    assert canvas._parse_voice_command("duvar kalınlığı kaç olmalı")[0][0][0] == "ask"
    assert canvas._parse_voice_command("bugün hava güzel")[0] == []


def _load(canvas, text):
    canvas._perform("design_request", text)
    deadline = time.time() + 10
    while canvas.design_busy and time.time() < deadline:
        time.sleep(0.02)
    time.sleep(0.05)
    canvas._drain_actions()


def test_design_flow_steps_guide_and_export(canvas):
    _load(canvas, "dron tasarla")
    assert canvas.design is not None and canvas.design_step == 0 and canvas._guide is not None

    nav = lambda t: [canvas._perform(*a) for a in canvas._parse_voice_command(t)[0]]  # noqa: E731
    nav("sonraki")
    assert canvas.design_step == 1
    nav("önceki adıma geri dön")
    assert canvas.design_step == 0
    for _ in range(10):
        nav("devam")
    assert canvas.design_step == len(canvas.design.parts) - 1   # son adımda durur

    frame = np.zeros((ac.FRAME_H, ac.FRAME_W, 3), np.uint8)
    canvas.board_mode = True
    view = canvas._compose(frame)
    x1, y1, x2, y2 = ac.GUIDE_RECT
    assert view[y1:y2, x1:x2].any()                     # rehber görünüyor
    canvas._draw_hud(view, 30.0)                          # panel çizimi hata vermemeli

    nav("rehberi gizle")
    assert not canvas._compose(frame)[y1:y2, x1:x2].any() or not canvas.show_guide

    nav("stl")
    assert canvas.last_export_dir and os.path.isdir(canvas.last_export_dir)
    nav("tasarımı kapat")
    assert canvas.design is None


def test_user_ink_stays_on_top_of_guide(canvas):
    _load(canvas, "120 fan")
    x1, y1, x2, y2 = ac.GUIDE_RECT
    cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
    canvas.canvas[cy - 3:cy + 3, cx - 3:cx + 3] = (255, 0, 0)
    view = canvas._compose(np.zeros((ac.FRAME_H, ac.FRAME_W, 3), np.uint8))
    assert tuple(view[cy, cx]) == (255, 0, 0)


def test_unknown_design_without_ai_explains_options(canvas):
    _load(canvas, "uzay istasyonu tasarla")
    assert canvas.design is None
    assert "tasarlayam" in canvas.assistant_tip


def test_queued_design_and_fullscreen_toggle(canvas):
    canvas.queue_design("tekerlek")
    assert canvas._pending_design == "tekerlek"
    assert canvas.fullscreen is True
    canvas._handle_key(ord("f"))
    assert canvas.fullscreen is False and canvas._window_dirty
