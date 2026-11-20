"""비전 V1: 이미지 ZIP·폴더 등록 → 보안 검사 → 구조·역할 추정·GT 짝·손상 표본 → 미리보기 → 비전 목표(역할 확정)."""

from __future__ import annotations

import io
import json
import sys
import time
import zipfile
from pathlib import Path
from urllib.parse import quote

import pytest

pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from mlworkbench.api import create_app  # noqa: E402

sys.path.insert(0, str(Path(__file__).parent))
from vision_samples import bmp_bytes, make_mvtec, png_bytes, tiff_bytes, zip_dir  # noqa: E402


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(tmp_path / "ws", serve_frontend=False, profile_in_process=False)) as c:
        c.ws, c.tmp = tmp_path / "ws", tmp_path
        yield c


def wait_ds(c, did, timeout=30):
    end = time.time() + timeout
    while time.time() < end:
        d = c.get(f"/api/datasets/{did}").json()
        if d["state"] in ("PROFILED", "PROFILE_FAILED", "UPLOAD_FAILED"):
            return d
        time.sleep(0.05)
    raise AssertionError("timeout")


def upload_zip(c, data: bytes, name="bottle.zip"):
    r = c.post("/api/datasets", content=data, headers={"X-File-Name": quote(name)})
    assert r.status_code == 201, r.text
    return wait_ds(c, r.json()["dataset_id"])


def mvtec_zip(c, **kw):
    base = make_mvtec(c.tmp / "src", **kw)
    return zip_dir(base)


# --- ZIP 등록·구조 -----------------------------------------------------------------
def test_zip_upload_profile(client):
    c = client
    d = upload_zip(c, mvtec_zip(c))
    assert d["state"] == "PROFILED" and d["modality"] == "vision" and d["image_count"] == 20 + 5 + 7 + 7
    ip = c.get(f"/api/datasets/{d['dataset_id']}/image-profile").json()
    roles = {f["path"]: f["role_guess"] for f in ip["folders"]}
    assert roles == {"bottle/train/good": "train_normal", "bottle/test/good": "test_normal",
                     "bottle/test/crack": "test_defect", "bottle/test/scratch": "test_defect",
                     "bottle/ground_truth/crack": "gt_mask", "bottle/ground_truth/scratch": "gt_mask"}
    crack = next(f for f in ip["folders"] if f["path"] == "bottle/test/crack")
    assert crack["defect_type"] == "crack" and crack["images"] == 4 and crack["samples"][0] == "bottle/test/crack/000.png"
    assert ip["formats"] == {"PNG": 39} and ip["sizes"] == [{"w": 48, "h": 48, "count": 39}]
    assert ip["channels"] == {"3": 32, "1": 7}
    assert ip["integrity"]["corrupt"] == 0 and ip["integrity"]["checked"] > 0
    assert ip["mask_pairs"] == {"defect_images": 7, "paired": 7}
    assert ip["skipped"]["non_image"] == 1  # license.txt
    assert (c.ws / "datasets" / d["dataset_id"] / "images" / "bottle" / "train" / "good" / "000.png").is_file()
    # 같은 ZIP을 다시 올리면 같은 버전(내용 해시)
    d2 = upload_zip(c, mvtec_zip(c))
    m1 = c.app.state.datasets.get(d["dataset_id"])
    m2 = c.app.state.datasets.get(d2["dataset_id"])
    assert m1.version == m2.version and m1.version.startswith("sha256:")
    assert "ground_truth_mask" in m1.artifacts


def test_preview_and_path_safety(client):
    c = client
    d = upload_zip(c, mvtec_zip(c))
    did = d["dataset_id"]
    r = c.get(f"/api/datasets/{did}/image", params={"path": "bottle/test/crack/000.png"})
    assert r.status_code == 200 and r.headers["content-type"] == "image/png" and r.content[:4] == b"\x89PNG"
    for bad in ("../mlwb.sqlite", "bottle/../../mlwb.sqlite", "/etc/passwd", "C:/Windows/win.ini",
                "bottle\\train\\good\\000.png", "bottle/license.txt", "nope.png", ""):
        assert c.get(f"/api/datasets/{did}/image", params={"path": bad}).status_code in (404, 422), bad


def test_zip_security(client):
    c = client
    good = png_bytes(8, 8)

    def make(entries):
        buf = io.BytesIO()
        with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, data in entries:
                zf.writestr(name, data)
        return buf.getvalue()

    d = upload_zip(c, make([("ok/a.png", good), ("../evil.png", good), ("/abs.png", good)]))
    assert d["state"] == "PROFILE_FAILED" and d["error"]["code"] == "ZIP_UNSAFE_ENTRY" and "2개" in d["error"]["message_ko"]
    assert not (c.ws / "evil.png").exists() and not (c.ws / "datasets" / d["dataset_id"] / "images").exists()
    # 심볼릭 링크 항목
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        info = zipfile.ZipInfo("link.png")
        info.external_attr = (0o120777 << 16)
        zf.writestr(info, "/etc/passwd")
        zf.writestr("x/a.png", good)
    assert upload_zip(c, buf.getvalue())["error"]["code"] == "ZIP_UNSAFE_ENTRY"
    # 압축 폭탄(작은 압축 크기, 큰 해제 크기)
    assert upload_zip(c, make([("big/b.png", b"\0" * (8 * 1024 * 1024))]))["error"]["code"] == "ZIP_BOMB"
    assert upload_zip(c, make([("doc/readme.txt", b"x")]))["error"]["code"] == "NO_IMAGES"
    assert upload_zip(c, b"PK\x03\x04broken")["error"]["code"] == "ZIP_INVALID"
    # 숨김·macOS 메타 파일은 무시
    d = upload_zip(c, make([("set/train/good/a.png", good), ("__MACOSX/set/._a.png", b"x"), ("set/.DS_Store", b"x")]))
    ip = c.get(f"/api/datasets/{d['dataset_id']}/image-profile").json()
    assert ip["image_count"] == 1 and ip["skipped"].get("hidden", 0) == 2


def test_corrupt_and_formats(client):
    c = client
    base = make_mvtec(c.tmp / "src", train=12, test_good=2, defects={"dent": 2}, masks=False)
    (base / "train" / "good" / "005.png").write_bytes(png_bytes(48, 48)[:200])  # 잘린 PNG
    (base / "test" / "good" / "x.bmp").write_bytes(bmp_bytes(40, 30))
    (base / "test" / "good" / "y.tif").write_bytes(tiff_bytes(24, 16))
    d = upload_zip(c, zip_dir(base))
    ip = c.get(f"/api/datasets/{d['dataset_id']}/image-profile").json()
    assert ip["integrity"]["corrupt"] == 1 and ip["integrity"]["corrupt_examples"] == ["bottle/train/good/005.png"]
    codes = {n["code"] for n in ip["notices"]}
    assert {"IMAGE_CORRUPT", "IMAGE_MIXED_SIZES", "IMAGE_NO_MASK"} <= codes
    assert ip["formats"]["BMP"] == 1 and ip["formats"]["TIFF"] == 1
    sizes = {(s["w"], s["h"]) for s in ip["sizes"]}
    assert (40, 30) in sizes and (24, 16) in sizes
    assert c.get(f"/api/datasets/{d['dataset_id']}/image", params={"path": "bottle/test/good/y.tif"}).status_code == 415
    assert c.get(f"/api/datasets/{d['dataset_id']}/image", params={"path": "bottle/test/good/x.bmp"}).status_code == 200


# --- 폴더 등록 ------------------------------------------------------------------------
def test_folder_registration_in_place(client):
    c = client
    base = make_mvtec(c.tmp / "user_data")
    before = sorted(p.as_posix() for p in base.rglob("*"))
    r = c.post("/api/datasets/folder", json={"path": str(base)})
    assert r.status_code == 201, r.json()
    assert str(base) not in r.text and str(c.tmp) not in r.text  # 응답에 절대경로 없음
    d = wait_ds(c, r.json()["dataset_id"])
    assert d["state"] == "PROFILED" and d["source_kind"] == "image_folder" and d["name"] == "bottle"
    assert not (c.ws / "datasets" / d["dataset_id"] / "images").exists()  # 복사하지 않음
    ip = c.get(f"/api/datasets/{d['dataset_id']}/image-profile")
    assert ip.json()["folders"][0]["path"] == "ground_truth/crack" and str(c.tmp) not in ip.text
    assert c.get(f"/api/datasets/{d['dataset_id']}/image", params={"path": "train/good/000.png"}).status_code == 200
    assert c.get("/api/datasets").status_code == 200 and str(base) not in c.get("/api/datasets").text
    # 삭제해도 사용자의 원래 폴더는 그대로
    r = c.request("DELETE", f"/api/datasets/{d['dataset_id']}", json={"confirmed": True})
    assert r.status_code == 200 and sorted(p.as_posix() for p in base.rglob("*")) == before
    for bad, code in (("relative/dir", "FOLDER_NOT_ABSOLUTE"), (str(c.tmp / "nope"), "FOLDER_NOT_FOUND"), ("", "FOLDER_REQUIRED"),
                      (str(c.ws), "FOLDER_NOT_ALLOWED"), (str(c.ws / "datasets"), "FOLDER_NOT_ALLOWED")):
        r = c.post("/api/datasets/folder", json={"path": bad})
        assert r.status_code == 400 and r.json()["code"] == code, (bad, r.json())


# --- 비전 목표(역할 확정) ---------------------------------------------------------------
def test_vision_goal(client):
    c = client
    d = upload_zip(c, mvtec_zip(c))
    did = d["dataset_id"]
    assert c.get(f"/api/datasets/{did}/goal").json()["saved"] is False  # 시계열 목표 화면과 섞이지 않음
    assert c.get(f"/api/datasets/{did}/vision-goal").json() == {"saved": False, "goal": None}
    ip = c.get(f"/api/datasets/{did}/image-profile").json()
    roles = {f["path"]: f["role_guess"] for f in ip["folders"]}
    r = c.put(f"/api/datasets/{did}/vision-goal", json={"roles": roles, "calibration": {"source": "split_from_train", "fraction": 0.1}})
    assert r.status_code == 200, r.json()
    out = r.json()
    assert out["counts"]["train_normal"] == 20 and out["counts"]["calibration_used"] == 2 and out["counts"]["train_after_split"] == 18
    assert out["counts"]["test_defect"] == 7 and out["counts"]["gt_mask"] == 7
    assert not [i for i in out["issues"] if i["severity"] != "info"]
    g = c.get(f"/api/datasets/{did}/vision-goal").json()
    assert g["saved"] and g["goal"]["roles"]["bottle/test/crack"] == "test_defect"
    # 오류: 정상 학습 부족, 보정 폴더 없음, 세그멘테이션, 없는 폴더
    few = {**roles, "bottle/train/good": "ignore", "bottle/test/good": "train_normal"}
    r = c.put(f"/api/datasets/{did}/vision-goal", json={"roles": few})
    assert r.status_code == 422 and {i["code"] for i in r.json()["issues"]} >= {"GOAL_TRAIN_TOO_FEW"}
    r = c.put(f"/api/datasets/{did}/vision-goal", json={"roles": roles, "calibration": {"source": "folder"}})
    assert r.status_code == 422 and "GOAL_CALIBRATION_MISSING" in {i["code"] for i in r.json()["issues"]}
    r = c.put(f"/api/datasets/{did}/vision-goal", json={"roles": roles, "task": "segmentation"})
    assert r.status_code == 422 and "GOAL_TASK_UNSUPPORTED" in {i["code"] for i in r.json()["issues"]}
    r = c.put(f"/api/datasets/{did}/vision-goal", json={"roles": {**roles, "nope/dir": "train_normal"}})
    assert r.status_code == 422 and "GOAL_UNKNOWN_FOLDER" in {i["code"] for i in r.json()["issues"]}
    r = c.put(f"/api/datasets/{did}/vision-goal", json={"roles": {**roles, "bottle/test/good": "boss"}})
    assert r.status_code == 422 and r.json()["code"] == "GOAL_INVALID"
    # 보정 폴더 사용
    cal = {**roles, "bottle/test/good": "calibration_normal"}
    r = c.put(f"/api/datasets/{did}/vision-goal", json={"roles": cal, "calibration": {"source": "folder"}})
    assert r.status_code == 200 and r.json()["counts"]["calibration_used"] == 5
    assert "GOAL_TEST_INCOMPLETE" in {i["code"] for i in r.json()["issues"]}  # 평가 정상이 없어짐(경고)


def test_vision_safe_profile_has_no_names(client):
    c = client
    d = upload_zip(c, mvtec_zip(c, category="secret_line_A", defects={"customer_xyz_crack": 3}))
    sp = c.app.state.datasets.get_safe_profile(d["dataset_id"])
    text = json.dumps(sp.model_dump(mode="json"), ensure_ascii=False)
    for word in ("secret_line_A", "customer_xyz_crack", "/good", "test/", ".png", str(c.ws)):
        assert word not in text, word
    assert sp.summary["images_by_role_guess"]["test_defect"] == 3 and sp.summary["defect_type_count"] == 1
    assert "folder_names" in sp.hidden_field_kinds
    from mlworkbench.mcp.catalog import ToolFacade

    out = ToolFacade(c.app.state.plans.registry, datasets=c.app.state.datasets).call(
        "dataset.get_profile", {"dataset_id": d["dataset_id"]}, context="recommendation")
    assert "customer_xyz_crack" not in json.dumps(out, ensure_ascii=False)


def test_retry_cannot_switch_modality(client):
    c = client
    d = upload_zip(c, b"PK\x03\x04broken")
    r = c.post(f"/api/datasets?retry_of={d['dataset_id']}", content=b"a,b\n1,2\n", headers={"X-File-Name": "x.csv"})
    assert r.status_code == 409 and r.json()["code"] == "MODALITY_MISMATCH"
    d2 = upload_zip(c, mvtec_zip(c))
    r = c.post(f"/api/datasets?retry_of={d['dataset_id']}", content=mvtec_zip(c), headers={"X-File-Name": "fixed.zip"})
    assert r.status_code == 201 and wait_ds(c, d["dataset_id"])["state"] == "PROFILED"
    assert d2["state"] == "PROFILED"
