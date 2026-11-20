"""합성 이미지 데이터(외부 다운로드 없음, Pillow 없이 PNG·BMP 직접 생성). MVTec AD 폴더 형식을 흉내 낸다.

<root>/<category>/train/good/000.png …
<root>/<category>/test/good/…, test/<불량 종류>/…
<root>/<category>/ground_truth/<불량 종류>/<이름>_mask.png
"""

from __future__ import annotations

import io
import struct
import zipfile
import zlib
from pathlib import Path


def png_bytes(w: int, h: int, *, channels: int = 3, seed: int = 0, spot: tuple[int, int, int] | None = None) -> bytes:
    """결정적 무늬 PNG. spot=(x, y, r)이면 밝은 원(불량 흉내)."""
    color = {1: 0, 3: 2, 4: 6}[channels]
    rows = []
    for y in range(h):
        row = bytearray([0])
        for x in range(w):
            v = (x * 7 + y * 13 + seed * 31) % 200 + 20
            if spot and (x - spot[0]) ** 2 + (y - spot[1]) ** 2 <= spot[2] ** 2:
                v = 255
            row += bytes([v] * channels) if channels != 4 else bytes([v, v, v, 255])
        rows.append(bytes(row))
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(t: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, color, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def mask_png(w: int, h: int, spot: tuple[int, int, int]) -> bytes:
    rows = []
    for y in range(h):
        rows.append(b"\x00" + bytes(255 if (x - spot[0]) ** 2 + (y - spot[1]) ** 2 <= spot[2] ** 2 else 0 for x in range(w)))
    raw = zlib.compress(b"".join(rows), 6)

    def chunk(t: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + t + body + struct.pack(">I", zlib.crc32(t + body) & 0xFFFFFFFF)

    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 0, 0, 0, 0))
            + chunk(b"IDAT", raw) + chunk(b"IEND", b""))


def bmp_bytes(w: int, h: int) -> bytes:
    row = (bytes([100, 150, 200]) * w).ljust((w * 3 + 3) // 4 * 4, b"\0")
    data = row * h
    header = b"BM" + struct.pack("<IHHI", 54 + len(data), 0, 0, 54)
    info = struct.pack("<IiiHHIIiiII", 40, w, h, 1, 24, 0, len(data), 2835, 2835, 0, 0)
    return header + info + data


def tiff_bytes(w: int, h: int) -> bytes:
    """비압축 8비트 회색 TIFF(머리글 읽기 확인용)."""
    pixels = bytes(w * h)
    entries = [(256, 3, 1, w), (257, 3, 1, h), (258, 3, 1, 8), (259, 3, 1, 1), (262, 3, 1, 1),
               (273, 4, 1, 8 + 2 + 12 * 9 + 4), (277, 3, 1, 1), (278, 3, 1, h), (279, 4, 1, len(pixels))]
    ifd = struct.pack("<H", len(entries))
    for tag, typ, cnt, val in entries:
        ifd += struct.pack("<HHI", tag, typ, cnt) + (struct.pack("<HH", val, 0) if typ == 3 else struct.pack("<I", val))
    ifd += struct.pack("<I", 0)
    return b"II*\x00" + struct.pack("<I", 8) + ifd + pixels


def make_mvtec(root: Path, *, category: str = "bottle", train: int = 20, test_good: int = 5,
               defects: dict[str, int] | None = None, size: int = 48, masks: bool = True) -> Path:
    defects = defects if defects is not None else {"crack": 4, "scratch": 3}
    base = root / category
    for i in range(train):
        p = base / "train" / "good" / f"{i:03d}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png_bytes(size, size, seed=i))
    for i in range(test_good):
        p = base / "test" / "good" / f"{i:03d}.png"
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_bytes(png_bytes(size, size, seed=100 + i))
    for k, (kind, n) in enumerate(defects.items()):
        for i in range(n):
            spot = (8 + (i * 5) % (size - 16), 10 + (k * 7) % (size - 20), 4)
            p = base / "test" / kind / f"{i:03d}.png"
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_bytes(png_bytes(size, size, seed=200 + i, spot=spot))
            if masks:
                m = base / "ground_truth" / kind / f"{i:03d}_mask.png"
                m.parent.mkdir(parents=True, exist_ok=True)
                m.write_bytes(mask_png(size, size, spot))
    (base / "license.txt").write_text("synthetic", encoding="utf-8")
    return base


def zip_dir(src: Path, *, extra: dict[str, bytes] | None = None) -> bytes:
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for p in sorted(src.rglob("*")):
            if p.is_file():
                zf.writestr(p.relative_to(src.parent).as_posix(), p.read_bytes())
        for name, data in (extra or {}).items():
            zf.writestr(name, data)
    return buf.getvalue()
