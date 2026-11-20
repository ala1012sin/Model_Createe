"""이미지 입력 어댑터 단위: 머리글(PNG·JPEG·BMP·TIFF), 구조 검사, 역할 추정, 상대 경로 안전."""

import io
import sys
from pathlib import Path

import pytest

from mlworkbench.datasets.image_folder import check_integrity, guess_role, image_header, safe_relpath

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "integration"))
from vision_samples import bmp_bytes, mask_png, png_bytes, tiff_bytes  # noqa: E402


def test_headers(tmp_path):
    cases = {"a.png": (png_bytes(30, 20), ("PNG", 30, 20, 3)), "g.png": (mask_png(9, 7, (2, 2, 1)), ("PNG", 9, 7, 1)),
             "a.bmp": (bmp_bytes(11, 5), ("BMP", 11, 5, 3)), "a.tif": (tiff_bytes(13, 6), ("TIFF", 13, 6, 1))}
    for name, (data, want) in cases.items():
        (tmp_path / name).write_bytes(data)
        assert image_header(tmp_path / name) == want, name
        assert check_integrity(tmp_path / name), name
    (tmp_path / "x.png").write_bytes(b"not an image")
    assert image_header(tmp_path / "x.png") is None and not check_integrity(tmp_path / "x.png")
    bad = bytearray(png_bytes(30, 20))
    bad[40] ^= 0xFF  # IDAT 내용 변조 → CRC 불일치
    (tmp_path / "crc.png").write_bytes(bytes(bad))
    assert not check_integrity(tmp_path / "crc.png")
    (tmp_path / "cut.png").write_bytes(png_bytes(30, 20)[:-12])  # IEND 없음
    assert not check_integrity(tmp_path / "cut.png")


def test_jpeg_header(tmp_path):
    Image = pytest.importorskip("PIL.Image")
    buf = io.BytesIO()
    Image.new("RGB", (37, 21), (10, 20, 30)).save(buf, "JPEG")
    (tmp_path / "a.jpg").write_bytes(buf.getvalue())
    assert image_header(tmp_path / "a.jpg") == ("JPEG", 37, 21, 3)
    assert check_integrity(tmp_path / "a.jpg")
    (tmp_path / "cut.jpg").write_bytes(buf.getvalue()[:-50])
    assert not check_integrity(tmp_path / "cut.jpg")


@pytest.mark.parametrize("path,want", [
    ("bottle/train/good", ("train_normal", None)),
    ("train/OK", ("train_normal", None)),
    ("bottle/test/good", ("test_normal", None)),
    ("bottle/test/broken_large", ("test_defect", "broken_large")),
    ("bottle/ground_truth/broken_large", ("gt_mask", None)),
    ("data/masks/test", ("gt_mask", None)),
    ("val/normal", ("calibration_normal", None)),
    ("train/defect", ("ignore", None)),
    ("random/folder", ("ignore", None)),
])
def test_guess_role(path, want):
    assert guess_role(path) == want


def test_safe_relpath(tmp_path):
    (tmp_path / "a").mkdir()
    (tmp_path / "a" / "x.png").write_bytes(png_bytes(4, 4))
    (tmp_path / "a" / "n.txt").write_text("x")
    (tmp_path / "outside.png").write_bytes(png_bytes(4, 4))
    root = tmp_path / "a"
    assert safe_relpath(root, "x.png") == (root / "x.png").resolve()
    for bad in ("../outside.png", "/x.png", "C:/x.png", "a\\x.png", "n.txt", ".hidden.png", "", "nope.png"):
        assert safe_relpath(root, bad) is None, bad
