"""이미지 데이터셋 입력 어댑터(V1, docs/P3_VISION_BRIEF.md): ZIP 안전 해제, 폴더 스캔, 이미지 머리글, 손상 표본 검사,
폴더 역할 추정, GT 마스크 짝.

외부 의존성 없이 PNG·JPEG·BMP·TIFF 머리글을 직접 읽는다(Pillow가 있으면 손상 검사에만 추가로 쓴다).
결과의 경로는 모두 등록 폴더 기준 상대 경로(POSIX). 절대경로는 만들지 않는다.
"""

from __future__ import annotations

import hashlib
import os
import random
import struct
import zipfile
import zlib
from collections import Counter, defaultdict
from pathlib import Path, PurePosixPath
from typing import Any

IMAGE_EXT = {".png": "PNG", ".jpg": "JPEG", ".jpeg": "JPEG", ".bmp": "BMP", ".tif": "TIFF", ".tiff": "TIFF"}
PREVIEW_EXT = {".png", ".jpg", ".jpeg", ".bmp"}  # 브라우저가 그릴 수 있는 형식
MAX_FILES = 200_000
MAX_UNCOMPRESSED = 20 * 1024**3
MAX_RATIO = 200
INTEGRITY_SAMPLE = 200
NORMAL_NAMES = {"good", "ok", "normal", "정상"}
GT_NAMES = {"ground_truth", "groundtruth", "gt", "mask", "masks", "label", "labels"}
TRAIN_NAMES = {"train", "training"}
VAL_NAMES = {"val", "valid", "validation", "calibration", "calib"}
TEST_NAMES = {"test", "testing", "eval"}


class ImageDataError(Exception):
    def __init__(self, code: str, message_ko: str, detail: dict | None = None):
        super().__init__(message_ko)
        self.code, self.message_ko, self.detail = code, message_ko, detail or {}


def _hidden(parts: tuple[str, ...]) -> bool:
    return any(p.startswith(".") or p == "__MACOSX" or p.startswith("._") or p.lower() == "thumbs.db" for p in parts)


# --- ZIP ------------------------------------------------------------------------------
def safe_extract_zip(zip_path: Path, out_dir: Path) -> dict[str, int]:
    """보안 검사 후 이미지 파일만 out_dir에 푼다. 검사 실패는 ImageDataError(사유·개수)."""
    try:
        zf = zipfile.ZipFile(zip_path)
    except (zipfile.BadZipFile, OSError):
        raise ImageDataError("ZIP_INVALID", "ZIP 파일을 열 수 없습니다(손상되었거나 ZIP이 아닙니다)") from None
    with zf:
        infos = zf.infolist()
        if len(infos) > MAX_FILES:
            raise ImageDataError("ZIP_TOO_MANY_FILES", f"ZIP 항목이 너무 많습니다(최대 {MAX_FILES:,}개)")
        unsafe, total, take = [], 0, []
        skipped = Counter()
        for info in infos:
            name = info.filename.replace("\\", "/")
            if info.is_dir():
                continue
            parts = PurePosixPath(name).parts
            if name.startswith("/") or ".." in parts or (parts and ":" in parts[0]):
                unsafe.append("경로")
                continue
            if (info.external_attr >> 16) & 0o170000 == 0o120000:
                unsafe.append("링크")
                continue
            if info.flag_bits & 0x1:
                raise ImageDataError("ZIP_ENCRYPTED", "암호가 걸린 ZIP은 지원하지 않습니다")
            if _hidden(parts):
                skipped["hidden"] += 1
                continue
            if PurePosixPath(name).suffix.lower() not in IMAGE_EXT:
                skipped["non_image"] += 1
                continue
            if info.file_size > 1024**2 and info.compress_size and info.file_size / info.compress_size > MAX_RATIO:
                raise ImageDataError("ZIP_BOMB", "압축률이 비정상적으로 높은 항목이 있어 풀지 않았습니다")
            total += info.file_size
            if total > MAX_UNCOMPRESSED:
                raise ImageDataError("ZIP_TOO_LARGE", f"압축을 푼 크기가 너무 큽니다(최대 {MAX_UNCOMPRESSED // 1024**3}GB)")
            take.append((info, parts))
        if unsafe:
            c = Counter(unsafe)
            raise ImageDataError("ZIP_UNSAFE_ENTRY",
                                 f"안전하지 않은 ZIP 항목 {len(unsafe)}개(" + ", ".join(f"{k} {v}" for k, v in c.items())
                                 + ")가 있어 풀지 않았습니다", {"count": len(unsafe)})
        if not take:
            raise ImageDataError("NO_IMAGES", "ZIP 안에 이미지(PNG·JPEG·BMP·TIFF)가 없습니다")
        root = out_dir.resolve()
        for info, parts in take:
            dest = root.joinpath(*parts)
            if root not in dest.resolve().parents:
                raise ImageDataError("ZIP_UNSAFE_ENTRY", "안전하지 않은 ZIP 항목이 있어 풀지 않았습니다")
            dest.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(dest, "wb") as out:
                written = 0
                while chunk := src.read(1024 * 1024):
                    written += len(chunk)
                    if written > info.file_size + 1024:  # 머리글 크기와 실제가 다르면 중단
                        raise ImageDataError("ZIP_BOMB", "ZIP 항목 크기가 머리글과 달라 풀지 않았습니다")
                    out.write(chunk)
        return {"extracted": len(take), "hidden": skipped["hidden"], "non_image": skipped["non_image"]}


# --- 머리글 ----------------------------------------------------------------------------
def image_header(path: Path) -> tuple[str, int, int, int] | None:
    """(형식, 너비, 높이, 채널 수). 읽을 수 없으면 None."""
    try:
        with open(path, "rb") as f:
            head = f.read(65536)
    except OSError:
        return None
    try:
        if head[:8] == b"\x89PNG\r\n\x1a\n" and head[12:16] == b"IHDR":
            w, h = struct.unpack(">II", head[16:24])
            color = head[25]
            ch = {0: 1, 2: 3, 3: 3, 4: 2, 6: 4}.get(color, 0)
            return "PNG", w, h, ch
        if head[:2] == b"\xff\xd8":
            i = 2
            while i + 9 < len(head):
                if head[i] != 0xFF:
                    i += 1
                    continue
                marker = head[i + 1]
                if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:
                    i += 2
                    continue
                seg = struct.unpack(">H", head[i + 2:i + 4])[0]
                if 0xC0 <= marker <= 0xCF and marker not in (0xC4, 0xC8, 0xCC):
                    h, w = struct.unpack(">HH", head[i + 5:i + 9])
                    return "JPEG", w, h, head[i + 9]
                i += 2 + seg
            return None
        if head[:2] == b"BM":
            w, h = struct.unpack("<ii", head[18:26])
            bits = struct.unpack("<H", head[28:30])[0]
            return "BMP", abs(w), abs(h), max(bits // 8, 1)
        if head[:4] in (b"II*\x00", b"MM\x00*"):
            e = "<" if head[:2] == b"II" else ">"
            off = struct.unpack(e + "I", head[4:8])[0]
            n = struct.unpack(e + "H", head[off:off + 2])[0]
            vals = {}
            for k in range(n):
                tag, typ, _cnt, val = struct.unpack(e + "HHII", head[off + 2 + 12 * k: off + 14 + 12 * k])
                if typ == 3:  # SHORT는 값 칸 앞 2바이트
                    val = struct.unpack(e + "H", head[off + 10 + 12 * k: off + 12 + 12 * k])[0]
                vals[tag] = val
            return "TIFF", vals.get(256, 0), vals.get(257, 0), vals.get(277, 1)
    except (struct.error, IndexError):
        return None
    return None


def check_integrity(path: Path) -> bool:
    """구조 검사(PNG 청크 CRC·IEND, JPEG SOI/EOI, BMP 크기). Pillow가 있으면 verify()도."""
    try:
        data = path.read_bytes()
    except OSError:
        return False
    ok = True
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        i, seen_end = 8, False
        while i + 12 <= len(data):
            ln = struct.unpack(">I", data[i:i + 4])[0]
            typ = data[i + 4:i + 8]
            body = data[i + 8:i + 8 + ln]
            if len(body) != ln or i + 12 + ln > len(data):
                ok = False
                break
            crc = struct.unpack(">I", data[i + 8 + ln:i + 12 + ln])[0]
            if zlib.crc32(typ + body) & 0xFFFFFFFF != crc:
                ok = False
                break
            i += 12 + ln
            if typ == b"IEND":
                seen_end = True
                break
        ok = ok and seen_end
    elif data[:2] == b"\xff\xd8":
        ok = b"\xff\xd9" in data[-1024:]
    elif data[:2] == b"BM":
        ok = len(data) >= 54 and struct.unpack("<I", data[2:6])[0] <= len(data)
    elif data[:4] in (b"II*\x00", b"MM\x00*"):
        ok = len(data) > 8
    else:
        ok = False
    if ok:
        try:
            from PIL import Image  # 선택 의존성

            with Image.open(path) as im:
                im.verify()
        except ImportError:
            pass
        except Exception:  # noqa: BLE001
            ok = False
    return ok


# --- 역할 추정 ---------------------------------------------------------------------------
def guess_role(rel_dir: str) -> tuple[str, str | None]:
    parts = [p.lower() for p in PurePosixPath(rel_dir).parts]
    leaf = parts[-1] if parts else ""
    if any(p in GT_NAMES for p in parts):
        return "gt_mask", None
    is_normal = leaf in NORMAL_NAMES
    if any(p in TRAIN_NAMES for p in parts):
        return ("train_normal", None) if is_normal else ("ignore", None)
    if any(p in VAL_NAMES for p in parts):
        return ("calibration_normal", None) if is_normal else ("ignore", None)
    if any(p in TEST_NAMES for p in parts):
        return ("test_normal", None) if is_normal else ("test_defect", PurePosixPath(rel_dir).name)
    return "ignore", None


# --- 스캔 -------------------------------------------------------------------------------
def scan(root: Path, *, seed: int = 0) -> dict[str, Any]:
    """등록 폴더를 읽어 프로파일 재료와 버전 해시를 만든다. 심볼릭 링크는 따라가지 않는다."""
    root = Path(root)
    if not root.is_dir():
        raise ImageDataError("FOLDER_NOT_FOUND", "등록한 폴더를 찾을 수 없습니다")
    files: list[str] = []
    skipped = Counter()
    for dirpath, dirnames, filenames in os.walk(root, followlinks=False):
        rel_dir = Path(dirpath).relative_to(root)
        dirnames[:] = sorted(d for d in dirnames if not _hidden((d,)) and not (Path(dirpath) / d).is_symlink())
        for fn in sorted(filenames):
            p = Path(dirpath) / fn
            if _hidden((fn,)):
                skipped["hidden"] += 1
                continue
            if p.is_symlink():
                skipped["unsafe"] += 1
                continue
            if p.suffix.lower() not in IMAGE_EXT:
                skipped["non_image"] += 1
                continue
            files.append((rel_dir / fn).as_posix())
            if len(files) > MAX_FILES:
                raise ImageDataError("TOO_MANY_FILES", f"이미지가 너무 많습니다(최대 {MAX_FILES:,}장)")
    if not files:
        raise ImageDataError("NO_IMAGES", "이미지(PNG·JPEG·BMP·TIFF)가 없습니다")
    by_dir: dict[str, list[str]] = defaultdict(list)
    for f in files:
        by_dir[PurePosixPath(f).parent.as_posix()].append(f)
    formats, sizes, channels = Counter(), Counter(), Counter()
    unreadable: list[str] = []
    version = hashlib.sha256()
    for f in files:  # 정렬된 순서
        p = root / f
        hd = image_header(p)
        if hd is None:
            unreadable.append(f)
        else:
            fmt, w, h, ch = hd
            formats[fmt] += 1
            sizes[(w, h)] += 1
            channels[str(ch)] += 1
        h = hashlib.sha256()
        with open(p, "rb") as fh:
            while chunk := fh.read(1024 * 1024):
                h.update(chunk)
        version.update(f.encode("utf-8") + b"\0" + h.digest())
    # 손상 표본: 폴더마다 고르게
    rng = random.Random(seed)
    per = max(1, INTEGRITY_SAMPLE // max(len(by_dir), 1))
    sample = []
    for d in sorted(by_dir):
        items = by_dir[d]
        sample += items if len(items) <= per else rng.sample(items, per)
    sample = sample[:INTEGRITY_SAMPLE]
    corrupt = [f for f in sample if f in unreadable or not check_integrity(root / f)]
    folders = []
    for d in sorted(by_dir):
        items = by_dir[d]
        role, defect = guess_role(d)
        folders.append({"path": d, "images": len(items),
                        "formats": dict(Counter(IMAGE_EXT[PurePosixPath(x).suffix.lower()] for x in items)),
                        "role_guess": role, "defect_type": defect, "samples": items[:3]})
    try:
        import PIL  # noqa: F401

        method = "구조 검사 + Pillow"
    except ImportError:
        method = "구조 검사"
    return {
        "image_count": len(files), "formats": dict(formats),
        "sizes": [{"w": w, "h": h, "count": c} for (w, h), c in sizes.most_common(5)],
        "channels": dict(channels), "folders": folders,
        "integrity": {"method": method, "checked": len(sample), "corrupt": len(corrupt),
                      "corrupt_examples": corrupt[:5], "unreadable_headers": len(unreadable)},
        "skipped": {k: int(v) for k, v in skipped.items()},
        "version": "sha256:" + version.hexdigest(),
    }


def pair_count(root: Path, folders: list[dict[str, Any]], roles: dict[str, str]) -> dict[str, int]:
    """역할 기준 불량 평가 이미지 수와 GT 마스크가 있는 수."""
    mask_stems: dict[str, set[str]] = defaultdict(set)
    for f in folders:
        if roles.get(f["path"]) == "gt_mask":
            kind = PurePosixPath(f["path"]).name
            d = root / f["path"]
            mask_stems[kind] |= {p.stem for p in d.iterdir() if p.suffix.lower() in IMAGE_EXT}
    defect, paired = 0, 0
    for f in folders:
        if roles.get(f["path"]) != "test_defect":
            continue
        kind = PurePosixPath(f["path"]).name
        d = root / f["path"]
        for p in d.iterdir():
            if p.suffix.lower() not in IMAGE_EXT or _hidden((p.name,)):
                continue
            defect += 1
            if p.stem in mask_stems[kind] or f"{p.stem}_mask" in mask_stems[kind]:
                paired += 1
    return {"defect_images": defect, "paired": paired}


def safe_relpath(root: Path, rel: str) -> Path | None:
    """화면이 요청한 상대 경로 → 등록 폴더 안의 이미지 파일 경로. 범위를 벗어나거나 이미지가 아니면 None."""
    if not rel or rel.startswith(("/", "\\")) or ":" in rel or "\\" in rel:
        return None
    parts = PurePosixPath(rel).parts
    if ".." in parts or _hidden(parts):
        return None
    base = root.resolve()
    p = (base.joinpath(*parts)).resolve()
    if base not in p.parents or not p.is_file() or p.suffix.lower() not in IMAGE_EXT:
        return None
    return p
