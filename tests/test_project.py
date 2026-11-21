"""Proje klasörü testleri (#24): bilgi.txt ölçüleri, referans STL'ler, fotoğraflar — ağ gerektirmez."""

import json
import os
import time

import numpy as np
import pytest

pytest.importorskip("manifold3d")
cv2 = pytest.importorskip("cv2")

from air_canvas import canvas as ac  # noqa: E402
from air_canvas import design as dsg  # noqa: E402
from air_canvas import project as prj  # noqa: E402
from air_canvas.ai import DesignAssistant  # noqa: E402

INFO = """# yorum satırı yok sayılır
pervane: 7 inç
kanat: 2
motor: 2806
kol: 15 cm
FC: 20 mm
vida: M4x16
Gövde karbon plaka üzerine oturacak.
"""


def _cube_stl(path, size=10.0, ascii_format=False, open_mesh=False):
    from manifold3d import Manifold
    mesh = Manifold.cube([size, size, size]).to_mesh()
    verts = np.asarray(mesh.vert_properties)[:, :3]
    tris = np.asarray(mesh.tri_verts)
    if open_mesh:
        tris = tris[:-1]
    if ascii_format:
        with open(path, "w") as fh:
            fh.write("solid kup\n")
            for t in tris:
                fh.write(" facet normal 0 0 0\n  outer loop\n")
                for v in verts[t]:
                    fh.write(f"   vertex {v[0]} {v[1]} {v[2]}\n")
                fh.write("  endloop\n endfacet\n")
            fh.write("endsolid kup\n")
    else:
        dsg.write_stl(path, verts, tris, "kup")


def test_parse_params_reads_sizes_and_units():
    params = prj.parse_params(INFO)
    assert params == {"prop_inch": 7.0, "blades": 2, "motor_size": "2806", "motor_spacing": 16.0,
                      "arm_length": 150.0, "fc_spacing": 20.0, "bolt_m": 4.0, "bolt_length": 16.0}
    assert prj.parse_params("motor: 1404")["motor_spacing"] == 9.0
    assert prj.parse_params("motor delik: 19 mm\nmotor: 2207")["motor_spacing"] == 19.0


def test_templates_use_project_sizes_unless_speech_says_otherwise():
    params = prj.parse_params(INFO)
    assert dsg.match_template("pervane", params)["name"] == "7 inç 2 kanatlı pervane"
    assert dsg.match_template("5 inç pervane", params)["name"] == "5 inç 2 kanatlı pervane"
    drone = dsg.Design(dsg.match_template("dron tasarla", params))
    assert drone.name == "Mini dron gövdesi" and "7 inç" in drone.summary
    arm = drone.parts[1]
    assert arm["shapes"][0]["size"][0] == 150.0                                  # kol 15 cm
    fc_holes = [sh["pos"][:2] for sh in drone.parts[0]["shapes"][1:5]]
    assert sorted({abs(round(c, 2)) for xy in fc_holes for c in xy}) == [10.0]   # FC 20 mm
    assert "M4x16" in dsg.match_template("vida", params)["name"]


def test_open_project_creates_folder_and_loads_files(tmp_path):
    empty = prj.open_project(str(tmp_path), "Dron")
    assert os.path.isfile(os.path.join(empty.folder, "bilgi.txt"))
    assert os.path.isdir(os.path.join(empty.folder, "foto")) and os.path.isdir(os.path.join(empty.folder, "referans"))
    assert empty.info == "" and empty.params == {}                               # şablon satırları yorum

    with open(os.path.join(empty.folder, "bilgi.txt"), "w", encoding="utf-8") as fh:
        fh.write(INFO)
    _cube_stl(os.path.join(empty.folder, "referans", "kamera_tutucu.stl"))
    _cube_stl(os.path.join(empty.folder, "referans", "ascii.stl"), ascii_format=True)
    _cube_stl(os.path.join(empty.folder, "referans", "delikli.stl"), open_mesh=True)
    cv2.imwrite(os.path.join(empty.folder, "foto", "on.jpg"), np.full((300, 400, 3), 200, np.uint8))

    project = prj.open_project(str(tmp_path), "dron")
    assert [n for n, _, _ in project.references] == ["ascii", "kamera_tutucu"]
    assert len(project.skipped) == 1 and "su geçirmez" in project.skipped[0]
    assert project.params["prop_inch"] == 7.0 and len(project.photos) == 1
    assert "karbon plaka" in project.context_text() and "kamera_tutucu" in project.context_text()
    assert prj.photo_mosaic(project.photos)[:4] == b"\x89PNG"


def test_read_stl_merges_vertices_into_a_closed_mesh(tmp_path):
    path = str(tmp_path / "kup.stl")
    _cube_stl(path)
    verts, tris = prj.read_stl(path)
    assert len(verts) == 8 and len(tris) == 12 and prj.is_watertight(tris)


class FakeLocal:
    available = True

    def __init__(self, design_reply=""):
        self.calls = []
        self.design_reply = design_reply

    def model_for(self, task):
        return {"vision": "qwen3-vl", "design": "coder"}.get(task, "coder")

    def chat(self, messages, model=None, **kwargs):
        self.calls.append((model, messages))
        if model == "qwen3-vl":
            return "Dört kollu X tipi gövde; kollar ortadaki plakaya ikişer vidayla bağlı."
        return self.design_reply

    @staticmethod
    def image_message(text, png):
        return {"role": "user", "content": [{"type": "text", "text": text},
                                            {"type": "image_url", "image_url": {"url": "data:,"}}]}


def _settle(c, until, timeout=10.0):
    end = time.time() + timeout
    while time.time() < end:
        c._drain_actions()
        if until():
            return
        time.sleep(0.05)
    pytest.fail("zaman aşımı")


def test_voice_opens_project_and_designs_with_its_sizes_and_parts(tmp_path):
    folder = prj.open_project(str(tmp_path), "dron").folder
    with open(os.path.join(folder, "bilgi.txt"), "w", encoding="utf-8") as fh:
        fh.write(INFO)
    _cube_stl(os.path.join(folder, "referans", "kamera_tutucu.stl"))
    cv2.imwrite(os.path.join(folder, "foto", "ust.png"), np.full((200, 200, 3), 120, np.uint8))

    local = FakeLocal()
    c = ac.AirCanvas(auto_init=False, save_dir=str(tmp_path),
                     assistant=DesignAssistant(local_llm=lambda: local, mode="local"))
    c.voice_feedback = False
    actions, _ = c._parse_voice_command("dron projesini aç")
    assert actions == [("project_open", "dron")]
    assert c._parse_voice_command("projeyi kapat")[0] == [("project_close", None)]
    c._perform(*actions[0])
    _settle(c, lambda: c.project is not None and c.project.photo_summary)
    assert "X tipi" in c.project.photo_summary and local.calls[0][0] == "qwen3-vl"
    assert c.design is not None and c.design.parts[0]["name"] == "kamera_tutucu"   # referans rehberde

    c._perform("design_request", "dron tasarla")
    _settle(c, lambda: c.design is not None and c.design.name == "Mini dron gövdesi")
    names = [p["name"] for p in c.design.parts]
    assert names[-1] == "kamera_tutucu" and c.design.parts[1]["shapes"][0]["size"][0] == 150.0
    out = dsg.export_design(c.design, str(tmp_path / "3D"))
    files = os.listdir(out)
    assert any(f.endswith("kamera_tutucu.stl") for f in files)
    with open(os.path.join(out, "tasarim.json"), encoding="utf-8") as fh:
        assert json.load(fh)["references"] == ["kamera_tutucu"]


def test_project_context_reaches_the_local_model(tmp_path):
    reply = json.dumps({"name": "Uçak", "summary": "", "print_tips": [], "parts": [{
        "name": "Kanat", "purpose": "", "step": "", "print_orientation": "", "placements": [],
        "shapes": [dsg._s("box", (100, 20, 3), (0, 0, 1.5)), dsg._s("box", (10, 10, 3), (0, 0, 4)),
                   dsg._s("cylinder", (3, 3, 10), (20, 0, 1.5), op="subtract")]}]})
    local = FakeLocal(reply)
    project = prj.Project(name="ucak", folder=str(tmp_path), info="kanat açıklığı: 600 mm")
    c = ac.AirCanvas(auto_init=False, save_dir=str(tmp_path),
                     assistant=DesignAssistant(local_llm=lambda: local, mode="local"))
    c.voice_feedback = False
    c.project = project
    c._perform("design_request", "uçak tasarla")
    _settle(c, lambda: c.design is not None)
    prompt = local.calls[-1][1][-1]["content"]
    assert "kanat açıklığı: 600 mm" in prompt and "uçak tasarla" in prompt
    assert len(local.calls) == 1                                    # bilgi.txt varken ölçü sorulmaz
