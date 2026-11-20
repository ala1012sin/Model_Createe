"""Application Services — UI·CLI·MCP가 공통으로 쓰는 계층.

DatasetService : 데이터셋 매니페스트 등록·상태 전이 (P0는 합성 매니페스트만)
PlanService    : 초안 리비전·검증·승인 (승인 토큰은 해시만 저장)
JobService     : 승인 검증 후 실행 등록, 상태·이벤트, 취소 요청 (worker는 P2)
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import re
import secrets
from pathlib import Path
from typing import IO, Any

import yaml
from pydantic import ValidationError

from mlworkbench.core.contracts import (
    PlanProposal,
    DATASET_TRANSITIONS,
    RUN_TRANSITIONS,
    TERMINAL_RUN_STATES,
    ApprovedSnapshot,
    ColumnInfo,
    DatasetError,
    DatasetManifest,
    DatasetState,
    ImageFolderInfo,
    ImageProfile,
    LocalProfile,
    ProfileNotice,
    SafeProfile,
    TimeseriesGoal,
    ValidationIssue,
    VisionGoal,
    DraftPlan,
    RunEvent,
    RunRecord,
    RunStage,
    RunState,
    RunStatus,
    ValidationResult,
    execution_view,
    utcnow_iso,
)
from mlworkbench.core.errors import ApprovalError, NotFound, StateTransitionError, SubmitRejected, WorkbenchError
from mlworkbench.core.hashing import sha256_of, short_ref
from mlworkbench.core.registry import PluginRegistry
from mlworkbench.core.validation import PlanValidator, pydantic_issues
from mlworkbench.datasets import image_folder, xlsx_table
from mlworkbench.datasets.csv_profile import ProfileFailure, profile_csv
from mlworkbench.datasets.safe_profile import build_image_safe_profile, build_safe_profile, safe_profile_from_manifest
from mlworkbench.storage.database import Workspace

log = logging.getLogger("mlworkbench.datasets")
CSV_SUFFIXES = {".csv", ".tsv", ".txt"}
XLSX_SUFFIXES = {".xlsx"}
ZIP_SUFFIXES = {".zip"}
SUPPORTED_HINT = "CSV(.csv·.tsv·.txt), 엑셀(.xlsx), 이미지 ZIP(.zip) 파일만 등록할 수 있습니다"
DEFAULT_MAX_UPLOAD_BYTES = 2 * 1024**3
_CHUNK = 1 << 20


def _new_id(prefix: str) -> str:
    return f"{prefix}_{secrets.token_hex(6)}"



def _code_of(registry: PluginRegistry, key: str) -> str | None:
    pid, _, ver = key.partition("@")
    spec = registry.get(pid, ver)
    return spec.code_sha256 if spec is not None else None


def plugin_code_changed(snap: ApprovedSnapshot, registry: PluginRegistry) -> str | None:
    """승인 당시 사용자 플러그인 코드 해시와 다르면 그 플러그인 key. 불러온 코드와 지금 디스크의 코드를 모두 본다
    (앱 실행 중 파일만 고친 경우도 제출 전에 막는다). 내장은 모두 None."""
    for key in snap.plugin_versions:
        pid, _, ver = key.partition("@")
        want = snap.plugin_code.get(key)
        if want != _code_of(registry, key) or want != registry.current_code_hash(pid, ver):
            return key
    return None

class DatasetService:
    """데이터셋 등록·상태·로컬 프로파일. S1(데이터셋 화면)이 호출하는 경로.

    파일은 workspace/datasets/<dataset_id>/source.csv(CSV) 또는 source.xlsx + table.csv(XLSX 선택 시트 정규화)로
    서버가 관리한다. 분석·학습·적용은 local_path(표)를 읽고, version은 그 표의 내용 해시다.
    버전은 저장한 바이트의 sha256. 원래 파일명은 화면 표시용(name)으로만 쓴다.
    """

    def __init__(self, ws: Workspace, *, max_upload_bytes: int | None = None):
        self.ws = ws
        self.max_upload_bytes = max_upload_bytes or int(os.environ.get("MLWB_MAX_UPLOAD_BYTES", DEFAULT_MAX_UPLOAD_BYTES))

    # 기본 저장 ---------------------------------------------------------------
    def register(self, manifest: DatasetManifest) -> DatasetManifest:
        with self.ws.session() as conn:
            conn.execute(
                "INSERT INTO datasets(dataset_id, manifest_json, state, updated_at) VALUES (?,?,?,?)",
                (manifest.dataset_id, manifest.model_dump_json(), manifest.state.value, utcnow_iso()),
            )
        return manifest

    def get(self, dataset_id: str) -> DatasetManifest:
        with self.ws.session() as conn:
            row = conn.execute("SELECT manifest_json FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
        if row is None:
            raise NotFound(f"데이터셋을 찾을 수 없습니다: {dataset_id}")
        return DatasetManifest.model_validate_json(row["manifest_json"])

    def find(self, dataset_id: str) -> DatasetManifest | None:
        try:
            return self.get(dataset_id)
        except NotFound:
            return None

    def list(self) -> list[DatasetManifest]:
        """S1 왼쪽 등록 목록(최근 등록 순, 재등록해도 순서 유지)."""
        with self.ws.session() as conn:
            rows = conn.execute("SELECT manifest_json FROM datasets ORDER BY rowid DESC").fetchall()
        return [DatasetManifest.model_validate_json(r["manifest_json"]) for r in rows]

    def list_entries(self) -> list[tuple[DatasetManifest, str]]:
        """(매니페스트, 현재 상태가 시작된 시각) — 화면의 경과 시간 표시용."""
        with self.ws.session() as conn:
            rows = conn.execute("SELECT manifest_json, updated_at FROM datasets ORDER BY rowid DESC").fetchall()
        return [(DatasetManifest.model_validate_json(r["manifest_json"]), r["updated_at"]) for r in rows]

    def state_since(self, dataset_id: str) -> str | None:
        with self.ws.session() as conn:
            row = conn.execute("SELECT updated_at FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
        return row["updated_at"] if row else None

    def recover_interrupted(self) -> list[str]:
        """서버가 업로드·분석 도중 종료된 경우: 진행 중 상태를 실패로 바꿔 재시도할 수 있게 한다."""
        fixed = []
        for m in self.list():
            if m.state == DatasetState.UPLOADING:
                self._remove(self.ws.root / "datasets" / m.dataset_id / ".upload.part")
                for leftover in ("source.xlsx",):
                    self._remove(self.ws.root / "datasets" / m.dataset_id / leftover)
                self._fail_upload(m.dataset_id, "INTERRUPTED", "업로드 중 앱이 종료되었습니다. 파일을 다시 선택해 주세요",
                                  self.ws.root / "datasets" / m.dataset_id / "source.csv")
                fixed.append(m.dataset_id)
            elif m.state == DatasetState.PROFILING:
                self._fail_profile(m.dataset_id, "INTERRUPTED", "분석 중 앱이 종료되었습니다. 다시 분석해 주세요", {})
                fixed.append(m.dataset_id)
        return fixed

    def transition(self, dataset_id: str, new_state: DatasetState, **updates: Any) -> DatasetManifest:
        m = self.get(dataset_id)
        if new_state not in DATASET_TRANSITIONS[m.state]:
            raise StateTransitionError(f"데이터셋 상태를 {m.state.value} → {new_state.value}로 바꿀 수 없습니다")
        m = DatasetManifest.model_validate({**m.model_dump(), "state": new_state, **updates})
        with self.ws.session() as conn:
            conn.execute(
                "UPDATE datasets SET manifest_json=?, state=?, updated_at=? WHERE dataset_id=?",
                (m.model_dump_json(), m.state.value, utcnow_iso(), dataset_id),
            )
        return m

    def _dir(self, dataset_id: str) -> Path:
        d = self.ws.root / "datasets" / dataset_id
        d.mkdir(parents=True, exist_ok=True)
        return d

    @staticmethod
    def _remove(path: Path) -> None:
        try:
            path.unlink(missing_ok=True)
        except OSError:  # 삭제가 막힌 위치: 레코드에서 참조하지 않으므로 사용되지 않음
            log.warning("임시 파일을 지우지 못했습니다: %s", path.name)

    # 파일 등록(CSV·XLSX) --------------------------------------------------------
    def register_csv(self, source: str | os.PathLike | IO[bytes], *, display_name: str | None = None,
                     retry_of: str | None = None) -> DatasetManifest:
        """CSV 또는 XLSX를 워크스페이스로 복사해 등록한다. 실패해도 예외 대신 UPLOAD_FAILED 매니페스트를 돌려준다.

        XLSX는 원본(source.xlsx)을 보관하고 시트 목록만 읽어 REGISTERED(시트 선택 대기)로 둔다.
        retry_of: UPLOAD_FAILED/PROFILE_FAILED 데이터셋에 새 파일로 다시 등록(명시적 재시도).
        """
        is_path = isinstance(source, (str, os.PathLike))
        name = _display_name(display_name or (Path(source).name if is_path else "upload.csv"))
        suffix = Path(name).suffix.lower()
        kind = "xlsx" if suffix in XLSX_SUFFIXES else "zip" if suffix in ZIP_SUFFIXES else "csv"
        modality = "vision" if kind == "zip" else "timeseries"
        if retry_of:
            old = self.get(retry_of)
            if old.modality != modality:
                raise StateTransitionError("표 데이터와 이미지 데이터는 서로 바꿔 다시 등록할 수 없습니다. 새로 등록해 주세요",
                                           code="MODALITY_MISMATCH")
            m = self.transition(retry_of, DatasetState.UPLOADING, name=name, error=None, version=None, source_kind=kind,
                                local_path=None, size_bytes=None, upload_progress=0.0, row_count=None, columns=[],
                                selected_sheet=None, xlsx=None, images=None, image_count=None, artifacts=[])
            self._drop_profile(retry_of)
        else:
            m = self.register(DatasetManifest(dataset_id=f"ds_{secrets.token_hex(6)}", name=name, modality=modality,
                                              source_kind=kind, state=DatasetState.UPLOADING, upload_progress=0.0))
        d = self._dir(m.dataset_id)
        part, final = d / ".upload.part", d / {"xlsx": "source.xlsx", "zip": "source.zip"}.get(kind, "source.csv")
        try:
            if (msg := xlsx_table.unsupported_message(suffix)) is not None:
                raise ProfileFailure("UNSUPPORTED_FILE_TYPE", msg)
            if suffix not in CSV_SUFFIXES | XLSX_SUFFIXES | ZIP_SUFFIXES:
                raise ProfileFailure("UNSUPPORTED_FILE_TYPE", SUPPORTED_HINT)
            total = os.path.getsize(source) if is_path else None
            digest, size = hashlib.sha256(), 0
            next_mark = 0.1
            src = open(source, "rb") if is_path else source
            try:
                with open(part, "wb") as out:
                    while chunk := src.read(_CHUNK):
                        size += len(chunk)
                        if size > self.max_upload_bytes:
                            raise ProfileFailure("FILE_TOO_LARGE",
                                                 f"파일이 너무 큽니다(최대 {self.max_upload_bytes // 1024**2}MB)")
                        digest.update(chunk)
                        out.write(chunk)
                        if total and size / total >= next_mark:
                            self._set_progress(m.dataset_id, min(size / total, 0.99))
                            next_mark += 0.1
            finally:
                if is_path:
                    src.close()
            if size == 0:
                raise ProfileFailure("EMPTY_FILE", "빈 파일입니다")
            os.replace(part, final)
            if kind == "xlsx":
                try:
                    book = xlsx_table.inspect(final)
                except xlsx_table.XlsxError as e:
                    raise ProfileFailure(e.code, e.message_ko) from None
        except ProfileFailure as e:
            self._remove(part)
            return self._fail_upload(m.dataset_id, e.code, e.message_ko, final)
        except FileNotFoundError:
            self._remove(part)
            return self._fail_upload(m.dataset_id, "SOURCE_NOT_FOUND", "선택한 파일을 찾을 수 없습니다", final)
        except OSError:
            self._remove(part)
            return self._fail_upload(m.dataset_id, "UPLOAD_IO_ERROR", "파일을 저장하는 중 오류가 났습니다. 저장 공간과 권한을 확인해 주세요", final)
        if kind == "zip":  # 이미지 ZIP: 분석 때 보안 검사 후 풀고 이미지 내용으로 버전을 정한다
            return self.transition(m.dataset_id, DatasetState.REGISTERED, version=None, size_bytes=size, local_path=None,
                                   upload_progress=1.0, images={"root_kind": "zip",
                                                                "source_sha256": "sha256:" + digest.hexdigest()})
        if kind == "xlsx":  # 시트 선택 전: 버전(정규화한 표의 해시)·분석 경로 없음
            return self.transition(m.dataset_id, DatasetState.REGISTERED, version=None, size_bytes=size, local_path=None,
                                   upload_progress=1.0, xlsx={"source_sha256": "sha256:" + digest.hexdigest(),
                                                              "sheets": book["sheets"], "epoch": book["epoch"],
                                                              "normalization": None})
        return self.transition(m.dataset_id, DatasetState.REGISTERED, version="sha256:" + digest.hexdigest(),
                               size_bytes=size, local_path=f"datasets/{m.dataset_id}/source.csv", upload_progress=1.0)

    def register_folder(self, folder: str) -> DatasetManifest:
        """로컬 이미지 폴더 등록(복사하지 않고 제자리 읽기 전용). 경로는 매니페스트에만(로컬 전용)."""
        raw = (folder or "").strip().strip('"')
        if not raw:
            raise WorkbenchError("폴더 경로를 입력해 주세요", code="FOLDER_REQUIRED")
        p = Path(raw).expanduser()
        if not p.is_absolute():
            raise WorkbenchError("전체 경로(예: C:\\data\\bottle)를 입력해 주세요", code="FOLDER_NOT_ABSOLUTE")
        if p.is_symlink() or not p.is_dir():
            raise WorkbenchError("폴더를 찾을 수 없습니다. 경로를 확인해 주세요", code="FOLDER_NOT_FOUND")
        root = p.resolve()
        if root == Path(root.anchor) or root == self.ws.root or self.ws.root in root.parents or root in self.ws.root.parents:
            raise WorkbenchError("드라이브 최상위·작업 공간 폴더는 등록할 수 없습니다. 이미지가 든 폴더를 골라 주세요",
                                       code="FOLDER_NOT_ALLOWED")
        name = _display_name(root.name or "images")
        return self.register(DatasetManifest(dataset_id=f"ds_{secrets.token_hex(6)}", name=name, modality="vision",
                                             source_kind="image_folder", state=DatasetState.REGISTERED,
                                             local_path=str(root), images={"root_kind": "folder"}))

    def image_root(self, m: DatasetManifest) -> Path:
        if m.source_kind == "image_folder":
            return Path(m.local_path or "")
        return self.ws.root / "datasets" / m.dataset_id / "images"

    def needs_sheet(self, m: DatasetManifest) -> bool:
        # 업로드 실패한 엑셀은 '업로드 실패'로 보인다(ADD-UX-05) — 시트를 고를 수 있는 등록 상태일 때만
        return m.source_kind == "xlsx" and not m.selected_sheet and m.state == DatasetState.REGISTERED

    def _update_manifest(self, dataset_id: str, **updates: Any) -> DatasetManifest:
        m = DatasetManifest.model_validate({**self.get(dataset_id).model_dump(), **updates})
        with self.ws.session() as conn:
            conn.execute("UPDATE datasets SET manifest_json=?, updated_at=? WHERE dataset_id=?",
                         (m.model_dump_json(), utcnow_iso(), dataset_id))
        return m

    def _has_dependents(self, dataset_id: str) -> bool:
        with self.ws.session() as conn:
            row = conn.execute("SELECT 1 FROM drafts WHERE json_extract(body_json, '$.dataset_id') = ? LIMIT 1",
                               (dataset_id,)).fetchone()
        return row is not None

    def select_sheet(self, dataset_id: str, sheet: str) -> DatasetManifest:
        """XLSX 시트 선택. 분석은 호출한 쪽이 이어서 예약한다(profile).

        - 목표·전처리·승인(초안)이 없는 데이터셋: 같은 데이터셋의 시트를 바꾼다(이전 프로파일은 버림).
        - 초안이 있는 데이터셋: 원본을 복사해 **새 데이터셋**으로 만든다. 기존 데이터셋·설정·승인·실행은 그대로 남는다.
        """
        m = self.get(dataset_id)
        if m.source_kind != "xlsx" or not m.xlsx:
            raise StateTransitionError("엑셀 데이터셋만 시트를 고를 수 있습니다", code="NOT_XLSX")
        if m.state not in (DatasetState.REGISTERED, DatasetState.PROFILED, DatasetState.PROFILE_FAILED):
            raise StateTransitionError("업로드·분석이 끝난 뒤 시트를 고를 수 있습니다", code="DATASET_BUSY")
        if sheet not in [s["name"] for s in m.xlsx["sheets"]]:
            raise NotFound("선택한 시트가 파일에 없습니다")
        if m.selected_sheet == sheet and m.state != DatasetState.PROFILE_FAILED:
            return m
        if m.selected_sheet and self._has_dependents(dataset_id):
            new_id = f"ds_{secrets.token_hex(6)}"
            d = self._dir(new_id)
            shutil.copyfile(self.ws.root / "datasets" / dataset_id / "source.xlsx", d / "source.xlsx")
            if _sha256_file(d / "source.xlsx") != m.xlsx["source_sha256"]:
                raise StateTransitionError("원본 엑셀 파일이 바뀌었습니다. 다시 등록해 주세요", code="SOURCE_CHANGED")
            return self.register(DatasetManifest(
                dataset_id=new_id, name=m.name, modality="timeseries", source_kind="xlsx", state=DatasetState.REGISTERED,
                size_bytes=m.size_bytes, upload_progress=1.0, selected_sheet=sheet,
                xlsx={**m.xlsx, "normalization": None, "copied_from": dataset_id}))
        # 같은 데이터셋에서 시트 변경: 이전 프로파일을 버리고 '등록됨(분석 대기)'으로 되돌린다
        self._drop_profile(dataset_id)
        new = DatasetManifest.model_validate({**m.model_dump(), "state": DatasetState.REGISTERED, "selected_sheet": sheet,
                                             "version": None, "local_path": None, "row_count": None, "columns": [],
                                             "error": None, "xlsx": {**m.xlsx, "normalization": None}})
        with self.ws.session() as conn:
            conn.execute("UPDATE datasets SET manifest_json=?, state=?, updated_at=? WHERE dataset_id=?",
                         (new.model_dump_json(), new.state.value, utcnow_iso(), dataset_id))
        return new

    def _set_progress(self, dataset_id: str, value: float) -> None:
        m = self.get(dataset_id)
        m = m.model_copy(update={"upload_progress": round(value, 3)})
        with self.ws.session() as conn:
            conn.execute("UPDATE datasets SET manifest_json=?, updated_at=? WHERE dataset_id=?",
                         (m.model_dump_json(), utcnow_iso(), dataset_id))

    def _fail_upload(self, dataset_id: str, code: str, message_ko: str, final: Path) -> DatasetManifest:
        self._remove(final)  # 재시도 전 파일도 남기지 않음(레코드는 경로를 갖지 않음)
        return self.transition(dataset_id, DatasetState.UPLOAD_FAILED, upload_progress=None, version=None,
                               local_path=None, size_bytes=None,
                               error=DatasetError(code=code, stage="upload", message_ko=message_ko))

    # 분석 -------------------------------------------------------------------
    def profile(self, dataset_id: str) -> DatasetManifest:
        """등록된 CSV를 로컬에서 분석한다. 실패하면 PROFILE_FAILED + 오류 코드를 돌려준다."""
        m = self.transition(dataset_id, DatasetState.PROFILING, error=None)
        self._drop_profile(dataset_id)
        if m.modality == "vision":
            return self._profile_images(m)
        try:
            if m.source_kind == "xlsx":
                m = self._normalize_xlsx(m)
            if not m.local_path or not m.version:
                raise ProfileFailure("SOURCE_MISSING", "저장된 원본 파일이 없습니다. 다시 등록해 주세요")
            path = self.ws.root / m.local_path
            if not path.exists():
                raise ProfileFailure("SOURCE_MISSING", "저장된 원본 파일이 없습니다. 다시 등록해 주세요")
            if _sha256_file(path) != m.version:
                raise ProfileFailure("SOURCE_CHANGED", "등록 이후 원본 파일이 바뀌었습니다. 다시 등록해 주세요")
            local = profile_csv(path, dataset_id=dataset_id, dataset_version=m.version,
                                profile_version=m.profile_version + 1)
            if m.source_kind == "xlsx":
                local = _xlsx_notices(local, m.xlsx["normalization"])
        except ProfileFailure as e:
            return self._fail_profile(dataset_id, e.code, e.message_ko, e.detail)
        except Exception as e:  # 예상 못 한 오류: 원문은 화면·요약에 싣지 않음
            log.exception("프로파일 중 예기치 않은 오류")
            return self._fail_profile(dataset_id, "PROFILE_INTERNAL_ERROR", "분석 중 예기치 않은 오류가 났습니다",
                                      {"exception_type": type(e).__name__})
        return self._save_profiled(m, local)

    def _profile_images(self, m: DatasetManifest) -> DatasetManifest:
        """이미지 ZIP(보안 검사 후 해제)·폴더를 읽어 S1-V 프로파일을 만든다(V1)."""
        try:
            root = self.image_root(m)
            if m.source_kind == "zip":
                src = self.ws.root / "datasets" / m.dataset_id / "source.zip"
                if not src.exists():
                    raise ProfileFailure("SOURCE_MISSING", "저장된 원본 ZIP이 없습니다. 다시 등록해 주세요")
                if _sha256_file(src) != (m.images or {}).get("source_sha256"):
                    raise ProfileFailure("SOURCE_CHANGED", "등록 이후 원본 ZIP이 바뀌었습니다. 다시 등록해 주세요")
                if root.exists():
                    shutil.rmtree(root)
                tmp = root.with_name("images.part")
                if tmp.exists():
                    shutil.rmtree(tmp)
                tmp.mkdir(parents=True)
                ext = image_folder.safe_extract_zip(src, tmp)
                os.replace(tmp, root)
            rep = image_folder.scan(root)
            if m.source_kind == "zip":  # 풀 때 건너뛴 항목(숨김·이미지 아님)도 함께 보여 준다
                for k in ("hidden", "non_image"):
                    if ext.get(k):
                        rep["skipped"][k] = rep["skipped"].get(k, 0) + ext[k]
            roles = {f["path"]: f["role_guess"] for f in rep["folders"]}
            pairs = image_folder.pair_count(root, rep["folders"], roles)
        except image_folder.ImageDataError as e:
            return self._fail_profile(m.dataset_id, e.code, e.message_ko, e.detail)
        except ProfileFailure as e:
            return self._fail_profile(m.dataset_id, e.code, e.message_ko, e.detail)
        except OSError:
            log.exception("이미지 분석 중 파일 오류")
            return self._fail_profile(m.dataset_id, "IMAGE_IO_ERROR", "이미지를 읽는 중 오류가 났습니다. 파일 권한·저장 공간을 확인해 주세요", {})
        notices = _image_notices(rep, roles, pairs)
        prof = ImageProfile(dataset_id=m.dataset_id, dataset_version=rep["version"], profile_version=m.profile_version + 1,
                            image_count=rep["image_count"], formats=rep["formats"], sizes=rep["sizes"],
                            channels=rep["channels"], folders=[ImageFolderInfo(**f) for f in rep["folders"]],
                            integrity=rep["integrity"], mask_pairs=pairs, skipped=rep["skipped"], notices=notices)
        new = DatasetManifest.model_validate({
            **m.model_dump(), "state": DatasetState.PROFILED, "error": None, "version": rep["version"],
            "image_count": rep["image_count"], "profile_version": prof.profile_version,
            "local_path": m.local_path if m.source_kind == "image_folder" else f"datasets/{m.dataset_id}/images",
            "artifacts": ["ground_truth_mask"] if any(r == "gt_mask" for r in roles.values()) else [],
        })
        self._commit_profile(m.dataset_id, prof.profile_version, prof.dataset_version, prof.model_dump_json(), new)
        return new

    def _commit_profile(self, dataset_id: str, pv: int, dv: str, profile_json: str, new: DatasetManifest) -> None:
        conn = self.ws.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT state FROM datasets WHERE dataset_id=?", (dataset_id,)).fetchone()
            if cur is None or cur["state"] != DatasetState.PROFILING.value:
                raise StateTransitionError("분석 도중 데이터셋 상태가 바뀌었습니다")
            conn.execute("INSERT OR REPLACE INTO profiles VALUES (?,?,?,?,?)", (dataset_id, pv, dv, profile_json, utcnow_iso()))
            conn.execute("UPDATE datasets SET manifest_json=?, state=?, updated_at=? WHERE dataset_id=?",
                         (new.model_dump_json(), new.state.value, utcnow_iso(), dataset_id))
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_image_profile(self, dataset_id: str) -> ImageProfile | None:
        m = self.get(dataset_id)
        if m.modality != "vision" or m.state != DatasetState.PROFILED:
            return None
        with self.ws.session() as conn:
            row = conn.execute("SELECT profile_json FROM profiles WHERE dataset_id=?", (dataset_id,)).fetchone()
        if row is None:
            return None
        ip = ImageProfile.model_validate_json(row["profile_json"])
        if ip.dataset_version != m.version or ip.profile_version != m.profile_version:
            return None
        return ip

    def _normalize_xlsx(self, m: DatasetManifest) -> DatasetManifest:
        """선택한 시트 → table.csv(공통 표). 버전 = 표 내용 해시. 원본 해시가 등록 때와 같아야 한다."""
        if not m.selected_sheet or not m.xlsx:
            raise ProfileFailure("SHEET_REQUIRED", "분석할 시트를 먼저 골라 주세요")
        d = self.ws.root / "datasets" / m.dataset_id
        src = d / "source.xlsx"
        if not src.exists():
            raise ProfileFailure("SOURCE_MISSING", "저장된 원본 파일이 없습니다. 다시 등록해 주세요")
        if _sha256_file(src) != m.xlsx["source_sha256"]:
            raise ProfileFailure("SOURCE_CHANGED", "등록 이후 원본 파일이 바뀌었습니다. 다시 등록해 주세요")
        try:
            rep = xlsx_table.sheet_to_csv(src, m.selected_sheet, d / "table.csv")
        except xlsx_table.XlsxError as e:
            raise ProfileFailure(e.code, e.message_ko) from None
        return self._update_manifest(m.dataset_id, version=rep["table_sha256"],
                                     local_path=f"datasets/{m.dataset_id}/table.csv",
                                     xlsx={**m.xlsx, "normalization": rep})

    def register_and_profile(self, source: str | os.PathLike | IO[bytes], *, display_name: str | None = None,
                             retry_of: str | None = None) -> DatasetManifest:
        m = self.register_csv(source, display_name=display_name, retry_of=retry_of)
        return self.profile(m.dataset_id) if m.state == DatasetState.REGISTERED else m

    def _fail_profile(self, dataset_id: str, code: str, message_ko: str, detail: dict) -> DatasetManifest:
        return self.transition(dataset_id, DatasetState.PROFILE_FAILED, row_count=None, columns=[],
                               error=DatasetError(code=code, stage="profile", message_ko=message_ko, detail=detail))

    def _drop_profile(self, dataset_id: str) -> None:
        with self.ws.session() as conn:
            conn.execute("DELETE FROM profiles WHERE dataset_id=?", (dataset_id,))

    def _save_profiled(self, m: DatasetManifest, local: LocalProfile) -> DatasetManifest:
        """프로파일 저장과 PROFILED 전이를 한 트랜잭션으로 묶는다."""
        new = DatasetManifest.model_validate({
            **m.model_dump(), "state": DatasetState.PROFILED, "error": None,
            "row_count": local.row_count, "profile_version": local.profile_version,
            "columns": [ColumnInfo(name=c.name, alias=c.alias, dtype=c.dtype, null_pct=c.null_pct) for c in local.columns],
        })
        conn = self.ws.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            cur = conn.execute("SELECT state FROM datasets WHERE dataset_id=?", (m.dataset_id,)).fetchone()
            if cur is None or cur["state"] != DatasetState.PROFILING.value:
                raise StateTransitionError("분석 도중 데이터셋 상태가 바뀌었습니다")
            conn.execute("INSERT OR REPLACE INTO profiles VALUES (?,?,?,?,?)",
                         (m.dataset_id, local.profile_version, local.dataset_version, local.model_dump_json(), utcnow_iso()))
            conn.execute("UPDATE datasets SET manifest_json=?, state=?, updated_at=? WHERE dataset_id=?",
                         (new.model_dump_json(), new.state.value, utcnow_iso(), m.dataset_id))
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        return new

    # 조회 -------------------------------------------------------------------
    def get_local_profile(self, dataset_id: str) -> LocalProfile | None:
        """S1 오른쪽 패널용. PROFILED이고 현재 버전과 일치할 때만 돌려준다."""
        m = self.get(dataset_id)
        if m.state != DatasetState.PROFILED or m.modality != "timeseries":
            return None
        with self.ws.session() as conn:
            row = conn.execute("SELECT profile_json FROM profiles WHERE dataset_id=?", (dataset_id,)).fetchone()
        if row is None:
            return None
        lp = LocalProfile.model_validate_json(row["profile_json"])
        if lp.dataset_version != m.version or lp.profile_version != m.profile_version:
            return None
        return lp

    def get_safe_profile(self, dataset_id: str) -> SafeProfile:
        """LLM/MCP 전송용 요약. 원래 헤더·파일명·시트 이름·수식·경로·셀 값 없음."""
        m = self.get(dataset_id)
        if m.source_kind in ("csv", "xlsx"):
            lp = self.get_local_profile(dataset_id)
            if lp is None:
                raise StateTransitionError("분석이 완료된 데이터셋만 요약을 만들 수 있습니다")
            return build_safe_profile(lp, source_kind=m.source_kind)
        if m.modality == "vision":
            ip = self.get_image_profile(dataset_id)
            if ip is None:
                raise StateTransitionError("분석이 완료된 데이터셋만 요약을 만들 수 있습니다")
            return build_image_safe_profile(ip, source_kind=m.source_kind)
        return safe_profile_from_manifest(m)


def _xlsx_notices(local: LocalProfile, rep: dict[str, Any]) -> LocalProfile:
    """정규화 결과를 화면 알림으로(로컬 전용). 코드만 SafeProfile에 들어간다."""
    notices = list(local.notices)
    if rep.get("formula_cells"):
        notices.append(ProfileNotice(code="XLSX_FORMULA_CACHED", severity="info",
                                     message_ko=f"수식 {rep['formula_cells']}칸은 파일에 저장된 계산 결과를 사용합니다. "
                                                "값이 최신인지는 Excel에서 다시 계산·저장해 확인해 주세요"))
    skipped = rep.get("blank_rows_skipped", 0) + rep.get("rows_above_header", 0)
    if skipped or rep.get("empty_columns_dropped"):
        notices.append(ProfileNotice(code="XLSX_BLANK_SKIPPED", severity="info",
                                     message_ko=f"빈 행 {skipped}개와 빈 열 {rep.get('empty_columns_dropped', 0)}개를 제외했습니다"))
    return local.model_copy(update={"notices": notices})


def _image_notices(rep: dict[str, Any], roles: dict[str, str], pairs: dict[str, int]) -> list[ProfileNotice]:
    out = []
    if not any(r == "train_normal" for r in roles.values()):
        out.append(ProfileNotice(code="IMAGE_NO_TRAIN_GUESS", severity="warning",
                                 message_ko="폴더 이름으로 '정상 학습' 폴더를 찾지 못했습니다. 아래 표에서 역할을 직접 골라 주세요"))
    if rep["integrity"]["corrupt"]:
        out.append(ProfileNotice(code="IMAGE_CORRUPT", severity="warning",
                                 message_ko=f"표본 {rep['integrity']['checked']}장 중 {rep['integrity']['corrupt']}장이 손상되었거나 읽을 수 없습니다",
                                 detail={"examples": rep["integrity"]["corrupt_examples"]}))
    if len(rep["sizes"]) > 1:
        out.append(ProfileNotice(code="IMAGE_MIXED_SIZES", severity="info",
                                 message_ko=f"이미지 크기가 {len(rep['sizes'])}가지 이상입니다. 학습 때 크기 맞춤(resize)이 필요합니다"))
    if pairs["defect_images"] and pairs["paired"] < pairs["defect_images"]:
        out.append(ProfileNotice(code="IMAGE_MASK_PARTIAL", severity="info",
                                 message_ko=f"불량 이미지 {pairs['defect_images']}장 중 {pairs['paired']}장만 GT 마스크 짝이 있습니다. "
                                            "픽셀 지표(AU-PRO)는 짝이 있는 이미지로만 계산합니다"))
    if pairs["defect_images"] and not pairs["paired"]:
        out.append(ProfileNotice(code="IMAGE_NO_MASK", severity="info",
                                 message_ko="GT 마스크가 없어 픽셀 지표(AU-PRO)는 '적용 불가(N/A)'로 표시됩니다"))
    return out


def _display_name(name: str) -> str:
    """화면 표시용 원래 파일명: 경로 성분·제어문자 제거."""
    base = re.split(r"[\\/]", name)[-1]
    base = "".join(ch for ch in base if ch.isprintable()).strip()
    return (base or "upload.csv")[:200]


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(_CHUNK):
            h.update(chunk)
    return "sha256:" + h.hexdigest()


class ApprovalReceipt(dict):
    """UI(호스트)에만 돌려주는 승인 영수증. LLM·MCP 응답에 넣지 않는다."""


class PlanService:
    def __init__(self, ws: Workspace, registry: PluginRegistry, datasets: DatasetService):
        self.ws, self.registry, self.datasets = ws, registry, datasets
        self.validator = PlanValidator(registry)

    # 초안 ------------------------------------------------------------------
    def create_draft(self, draft: DraftPlan) -> tuple[str, int]:
        draft_id = _new_id("draft")
        self._save(draft_id, 1, draft)
        return draft_id, 1

    def _save(self, draft_id: str, revision: int, draft: DraftPlan) -> None:
        with self.ws.session() as conn:
            conn.execute(
                "INSERT INTO drafts(draft_id, revision, body_json, exec_hash, created_at) VALUES (?,?,?,?,?)",
                (draft_id, revision, draft.model_dump_json(), sha256_of(execution_view(draft)), utcnow_iso()),
            )

    def get_draft(self, draft_id: str, revision: int | None = None) -> tuple[DraftPlan, int, str]:
        with self.ws.session() as conn:
            if revision is None:
                row = conn.execute(
                    "SELECT * FROM drafts WHERE draft_id=? ORDER BY revision DESC LIMIT 1", (draft_id,)
                ).fetchone()
            else:
                row = conn.execute("SELECT * FROM drafts WHERE draft_id=? AND revision=?", (draft_id, revision)).fetchone()
        if row is None:
            raise NotFound(f"초안을 찾을 수 없습니다: {draft_id}")
        return DraftPlan.model_validate_json(row["body_json"]), row["revision"], row["exec_hash"]

    def update_draft(self, draft_id: str, draft: DraftPlan, *, expected_revision: int) -> dict[str, Any]:
        """새 리비전 생성. 실행 설정이 바뀌면 해당 초안의 승인을 무효화한다.
        요약·추천 이유 같은 설명 텍스트만 바뀌면 승인은 유지된다."""
        _, latest, old_hash = self.get_draft(draft_id)
        if expected_revision != latest:
            raise StateTransitionError("다른 곳에서 초안이 수정되었습니다. 최신 상태를 다시 불러오세요",
                                       code="REVISION_CONFLICT")
        new_rev = latest + 1
        self._save(draft_id, new_rev, draft)
        changed = sha256_of(execution_view(draft)) != old_hash
        invalidated = 0
        if changed:
            with self.ws.session() as conn:
                invalidated = conn.execute(
                    "UPDATE approvals SET status='invalidated' WHERE draft_id=? AND status='active'", (draft_id,)
                ).rowcount
        return {"revision": new_rev, "execution_changed": changed, "approvals_invalidated": invalidated}

    # 목표 (S1 하단) ----------------------------------------------------------
    def latest_draft_for_dataset(self, dataset_id: str) -> tuple[str, DraftPlan, int] | None:
        with self.ws.session() as conn:
            row = conn.execute(
                "SELECT draft_id, revision, body_json FROM drafts WHERE json_extract(body_json, '$.dataset_id') = ? "
                "ORDER BY created_at DESC, revision DESC LIMIT 1", (dataset_id,)).fetchone()
        if row is None:
            return None
        return row["draft_id"], DraftPlan.model_validate_json(row["body_json"]), row["revision"]

    def save_goal(self, dataset_id: str, goal: "TimeseriesGoal") -> dict[str, Any]:
        """예측 목표를 검증해 초안에 저장한다. 목표 관련 오류가 있으면 저장하지 않는다.

        목표 외 항목(모델·분할 등)은 이후 화면에서 채우므로 여기서는 검사하지 않는다."""
        ds = self.datasets.get(dataset_id)
        existing = self.latest_draft_for_dataset(dataset_id)
        base = existing[1] if existing else DraftPlan(dataset_id=dataset_id, modality=ds.modality, task=goal.task,
                                                      source="goal")
        draft = base.model_copy(update={"user_goal": goal, "task": goal.task})
        result, _ = self.validator.validate(draft, mode="final", dataset=ds)
        issues = [i for i in result.issues if i.path.startswith("user_goal") or i.code.startswith("DATASET_")
                  or i.code.startswith("GOAL_")]
        if any(i.severity == "error" for i in issues):
            return {"saved": False, "issues": issues}
        if existing:
            draft_id, _, rev = existing
            info = self.update_draft(draft_id, draft, expected_revision=rev)
            revision = info["revision"]
        else:
            draft_id, revision = self.create_draft(draft)
        return {"saved": True, "draft_id": draft_id, "revision": revision, "issues": issues}

    # S1-V 비전 목표 ------------------------------------------------------------
    MIN_TRAIN_IMAGES = 10

    def check_vision_goal(self, dataset_id: str, goal: "VisionGoal") -> tuple[list[ValidationIssue], dict[str, int]]:
        """폴더 역할·보정·태스크 검사(규칙 9: 학습·보정·평가·GT 역할 분리). (issues, 역할별 장수·분리 후 수)"""
        ip = self.datasets.get_image_profile(dataset_id)
        if ip is None:
            raise StateTransitionError("분석이 끝난 이미지 데이터셋에서만 목표를 저장할 수 있습니다", code="DATASET_NOT_READY")
        folders = {f.path: f for f in ip.folders}
        issues: list[ValidationIssue] = []

        def add(code, sev, path, msg, hint=None):
            issues.append(ValidationIssue(code=code, severity=sev, path=path, message_ko=msg, fix_hint_ko=hint))

        unknown = [k for k in goal.roles if k not in folders]
        if unknown:
            add("GOAL_UNKNOWN_FOLDER", "error", "user_goal.roles", f"데이터셋에 없는 폴더입니다: {', '.join(unknown[:3])}")
        roles = {path: goal.roles.get(path, f.role_guess) for path, f in folders.items()}
        count = {r: sum(folders[p].images for p, rr in roles.items() if rr == r) for r in
                 ("train_normal", "calibration_normal", "test_normal", "test_defect", "gt_mask", "ignore")}
        if goal.task != "anomaly_detection":
            add("GOAL_TASK_UNSUPPORTED", "error", "user_goal.task",
                "세그멘테이션은 픽셀 라벨로 지도학습하는 태스크라 아직 지원하지 않습니다. 정상 기반 이상탐지를 고르세요")
        train = count["train_normal"]
        calib = count["calibration_normal"]
        if goal.calibration.source == "split_from_train":
            n_cal = max(1, round(train * goal.calibration.fraction)) if train else 0
            if calib:
                add("GOAL_CALIBRATION_FOLDER_UNUSED", "warning", "user_goal.calibration",
                    "'보정 정상' 폴더가 있지만 정상 학습에서 떼어 내도록 골랐습니다. 그 폴더는 쓰지 않습니다",
                    "보정 방식을 '보정 정상 폴더 사용'으로 바꾸세요")
            train_after, calib_used = train - n_cal, n_cal
        else:
            if not calib:
                add("GOAL_CALIBRATION_MISSING", "error", "user_goal.calibration",
                    "'보정 정상' 역할 폴더가 없습니다", "폴더 하나를 '보정 정상'으로 고르거나 '정상 학습에서 떼어 내기'를 고르세요")
            train_after, calib_used = train, calib
        if train_after < self.MIN_TRAIN_IMAGES:
            add("GOAL_TRAIN_TOO_FEW", "error", "user_goal.roles",
                f"정상 학습 이미지가 {train_after}장입니다(보정 분리 후). 최소 {self.MIN_TRAIN_IMAGES}장이 필요합니다",
                "정상 학습 역할 폴더를 확인하세요")
        if not count["test_normal"] or not count["test_defect"]:
            add("GOAL_TEST_INCOMPLETE", "warning", "user_goal.roles",
                "평가에 정상과 불량 이미지가 모두 있어야 AUROC·불량 검출률·정상 오판율을 계산할 수 있습니다")
        if not count["gt_mask"]:
            add("GOAL_NO_GT", "info", "user_goal.roles", "GT 마스크가 없어 픽셀 지표(AU-PRO)는 '적용 불가(N/A)'로 표시됩니다")
        if count["gt_mask"] and any(r == "gt_mask" for r in roles.values()) and not count["test_defect"]:
            add("GOAL_GT_WITHOUT_DEFECT", "warning", "user_goal.roles", "GT 마스크가 있지만 '불량 평가' 폴더가 없습니다")
        return issues, {**count, "train_after_split": train_after, "calibration_used": calib_used}

    def save_vision_goal(self, dataset_id: str, goal: "VisionGoal") -> dict[str, Any]:
        ds = self.datasets.get(dataset_id)
        if ds.modality != "vision" or ds.state != DatasetState.PROFILED:
            raise StateTransitionError("분석이 끝난 이미지 데이터셋에서만 목표를 저장할 수 있습니다", code="DATASET_NOT_READY")
        issues, counts = self.check_vision_goal(dataset_id, goal)
        if any(i.severity == "error" for i in issues):
            return {"saved": False, "issues": issues, "counts": counts}
        existing = self.latest_draft_for_dataset(dataset_id)
        base = existing[1] if existing else DraftPlan(dataset_id=dataset_id, modality="vision", task=goal.task, source="goal")
        draft = base.model_copy(update={"user_goal": goal, "task": goal.task})
        if existing:
            draft_id, _, rev = existing
            revision = self.update_draft(draft_id, draft, expected_revision=rev)["revision"]
        else:
            draft_id, revision = self.create_draft(draft)
        return {"saved": True, "draft_id": draft_id, "revision": revision, "issues": issues, "counts": counts}

    # S2 전처리 ---------------------------------------------------------------
    # 초안에 적용할 수 없는 문제(미등록·버전 불일치·종류/데이터/태스크 비호환·중복·형식 오류).
    # 미구현(PLUGIN_NOT_READY)·파라미터·순서 문제는 초안에 저장하고 화면에 표시한다(최종 검증에서 차단).
    APPLY_BLOCKING = frozenset({"SCHEMA_INVALID", "UNKNOWN_PLUGIN", "UNKNOWN_VERSION", "WRONG_PLUGIN_KIND",
                                "INCOMPATIBLE_MODALITY", "INCOMPATIBLE_TASK", "DUPLICATE_STEP"})

    def validate_plan(self, draft: DraftPlan) -> list:
        ds = self.datasets.find(draft.dataset_id)
        result, _ = self.validator.validate(draft, mode="draft", dataset=ds)
        return result.issues

    def _blocking(self, issues: list) -> list:
        return [i for i in issues if i.code in self.APPLY_BLOCKING]

    def _require_plan(self, dataset_id: str) -> tuple[str, DraftPlan, int]:
        found = self.latest_draft_for_dataset(dataset_id)
        if not found or found[1].user_goal is None:
            raise StateTransitionError("데이터셋 화면에서 예측 목표를 먼저 저장해 주세요", code="GOAL_REQUIRED")
        return found

    def save_steps(self, dataset_id: str, *, steps: list, excluded: list, expected_revision: int,
                   source: str | None = None) -> dict[str, Any]:
        """S2 편집 저장. 적용할 수 없는 도구가 있으면 저장하지 않는다."""
        draft_id, base, rev = self._require_plan(dataset_id)
        if rev != expected_revision:
            raise StateTransitionError("다른 곳에서 초안이 수정되었습니다. 최신 상태를 다시 불러오세요",
                                       code="REVISION_CONFLICT")
        update: dict[str, Any] = {"preprocessing": steps, "excluded_steps": excluded}
        if source:
            update["source"] = source
        if source == "manual":  # 수동 모드를 고르면 이전 추천 출처를 끊는다
            update["recommendation_id"] = None
        draft = base.model_copy(update=update)
        draft = DraftPlan.model_validate(draft.model_dump())
        issues = self.validate_plan(draft)
        # 체크 해제 항목도 등록된 호환 도구만 보관
        ex_issues = self.validate_plan(base.model_copy(update={"preprocessing": excluded}))
        blocking = self._blocking(issues) + [
            i.model_copy(update={"path": i.path.replace("preprocessing[", "excluded_steps[", 1)})
            for i in self._blocking(ex_issues) if i.path.startswith("preprocessing[")]
        if blocking:
            return {"saved": False, "issues": blocking}
        info = self.update_draft(draft_id, draft, expected_revision=rev)
        return {"saved": True, "draft_id": draft_id, "revision": info["revision"], "issues": issues}

    def apply_recommendation(self, dataset_id: str, rec: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        """저장된 추천을 초안에 적용(기존 전처리·모델·분할·평가 교체, 목표 유지).
        추천 당시 데이터 버전과 현재 버전이 다르면 거부. 적용 불가 도구는 빼고 사유를 돌려준다."""
        draft_id, base, rev = self._require_plan(dataset_id)
        m = self.datasets.get(dataset_id)
        if rec["dataset_id"] != dataset_id or rec["dataset_version"] != m.version:
            raise StateTransitionError("추천 이후 데이터가 바뀌었습니다. 다시 추천받으세요", code="RECOMMENDATION_STALE")
        if rev != expected_revision:
            raise StateTransitionError("다른 곳에서 초안이 수정되었습니다. 최신 상태를 다시 불러오세요",
                                       code="REVISION_CONFLICT")
        prop = PlanProposal.model_validate(rec["proposal"])
        if prop.user_goal != base.user_goal:
            raise StateTransitionError("추천 이후 예측 목표가 바뀌었습니다. 다시 추천받으세요", code="RECOMMENDATION_STALE")
        candidate = base.model_copy(update={
            "preprocessing": prop.preprocessing, "model": prop.model, "train_budget": prop.train_budget,
            "split_policy": prop.split_policy, "evaluation": prop.evaluation, "excluded_steps": [],
            "rationale": prop.rationale, "source": "llm", "recommendation_id": rec["recommendation_id"]})
        issues = self.validate_plan(DraftPlan.model_validate(candidate.model_dump()))
        bad_idx = {int(i.path.split("[")[1].split("]")[0]) for i in self._blocking(issues)
                   if i.path.startswith("preprocessing[")}
        skipped = [{"plugin_id": prop.preprocessing[i].plugin_id, "reason_ko": next(
            x.message_ko for x in issues if x.path.startswith(f"preprocessing[{i}]") and x.code in self.APPLY_BLOCKING)}
            for i in sorted(bad_idx)]
        kept = [s for i, s in enumerate(prop.preprocessing) if i not in bad_idx]
        model_bad = any(i.path.startswith("model") and i.code in self.APPLY_BLOCKING for i in issues)
        candidate = DraftPlan.model_validate({**candidate.model_dump(), "preprocessing": [k.model_dump() for k in kept],
                                              "model": None if model_bad else candidate.model_dump()["model"]})
        info = self.update_draft(draft_id, candidate, expected_revision=rev)
        return {"saved": True, "draft_id": draft_id, "revision": info["revision"], "skipped": skipped,
                "issues": self.validate_plan(candidate)}

    # S3 학습 설정 --------------------------------------------------------------
    TRAINING_PATHS = ("model", "train_budget", "split_policy", "evaluation")

    def training_candidate(self, dataset_id: str, fields: dict[str, Any]) -> tuple[DraftPlan | None, list]:
        """기존 초안에 학습 설정 필드만 바꾼 후보. 형식 오류면 (None, issues)."""
        _, base, _ = self._require_plan(dataset_id)
        data = base.model_dump(mode="json")
        data.update({k: fields.get(k) for k in self.TRAINING_PATHS})
        try:
            return DraftPlan.model_validate(data), []
        except ValidationError as e:
            issues = [i for i in pydantic_issues(e) if i.path.startswith(self.TRAINING_PATHS)] or pydantic_issues(e)
            return None, [self._percent_wording(i) for i in issues]

    @staticmethod
    def _percent_wording(i):
        """화면은 분할 비율을 %로 입력받으므로 메시지도 %로 맞춘다."""
        if i.path == "split_policy" and "합은 1" in i.message_ko:
            return i.model_copy(update={"message_ko": "학습·검증·테스트 비율의 합이 100%가 되어야 합니다"})
        if i.path in ("split_policy.train", "split_policy.test"):
            return i.model_copy(update={"message_ko": "0%보다 크고 100%보다 작아야 합니다"})
        if i.path == "split_policy.val":
            return i.model_copy(update={"message_ko": "0% 이상 100% 미만이어야 합니다"})
        return i

    def training_issues(self, draft: DraftPlan) -> list:
        return [i for i in self.validate_plan(draft) if i.path.startswith(self.TRAINING_PATHS)]

    def save_training(self, dataset_id: str, fields: dict[str, Any], *, expected_revision: int) -> dict[str, Any]:
        """S3 저장: 모델·학습량·분할·평가만 바꾸고 목표·전처리·제외 항목·추천 이력은 그대로 둔다."""
        draft_id, _, rev = self._require_plan(dataset_id)
        if rev != expected_revision:
            raise StateTransitionError("다른 곳에서 초안이 수정되었습니다. 최신 상태를 다시 불러오세요",
                                       code="REVISION_CONFLICT")
        draft, schema_issues = self.training_candidate(dataset_id, fields)
        if draft is None:
            return {"saved": False, "issues": schema_issues}
        issues = self.training_issues(draft)
        blocking = [i for i in issues if i.code in self.APPLY_BLOCKING and i.path.startswith("model")]
        if blocking:
            return {"saved": False, "issues": blocking}
        info = self.update_draft(draft_id, draft, expected_revision=rev)
        return {"saved": True, "draft_id": draft_id, "revision": info["revision"], "issues": issues}

    # 검증 ------------------------------------------------------------------
    def validate_draft(self, draft_id: str, *, mode: str = "draft") -> ValidationResult:
        draft, _, _ = self.get_draft(draft_id)
        result, _ = self.validator.validate(draft, mode=mode, dataset=self.datasets.find(draft.dataset_id))
        return result

    # 승인 ------------------------------------------------------------------
    def approve(self, draft_id: str, revision: int, *, approved_by: str) -> ApprovalReceipt:
        draft, latest, _ = self.get_draft(draft_id)
        if revision != latest:
            raise ApprovalError("초안이 그 사이에 바뀌었습니다. 최신 상태를 다시 불러와 확인해 주세요", code="STALE_REVISION")
        dataset = self.datasets.find(draft.dataset_id)
        if dataset is None:
            raise ApprovalError("등록되지 않은 데이터셋입니다", code="DATASET_NOT_FOUND")
        result, exe = self.validator.validate(draft, mode="final", dataset=dataset)
        if not result.ok or exe is None:
            err = ApprovalError("실행을 막는 오류가 있어 확정할 수 없습니다", code="VALIDATION_FAILED")
            err.issues = result.issues  # type: ignore[attr-defined]
            raise err
        plan_id = _new_id("plan")
        approval_id = "approve_" + secrets.token_urlsafe(24)
        plugins = [f"{s.plugin_id}@{s.plugin_version}" for s in exe.preprocessing] + [
            f"{exe.model.plugin_id}@{exe.model.plugin_version}"
        ]
        code = {k: c for k in plugins if (c := _code_of(self.registry, k))}
        stale = [k for k, c in code.items() if self.registry.current_code_hash(*k.split("@")) != c]
        if stale:
            raise ApprovalError(f"앱이 불러온 뒤 사용자 플러그인 코드가 바뀌었습니다: {', '.join(stale)}. "
                                "앱을 다시 시작해 새 코드를 불러온 뒤 확정하세요", code="PLUGIN_CODE_CHANGED")
        snap = ApprovedSnapshot(
            plan_id=plan_id,
            plan_hash=result.plan_hash,  # type: ignore[arg-type]
            dataset_id=exe.dataset_id,
            dataset_version=exe.dataset_version,
            draft_id=draft_id,
            draft_revision=revision,
            plugin_versions=plugins,
            plugin_code=code,
            approved_by=approved_by,
            approved_at=utcnow_iso(),
            immutable_config=exe.execution_config(),
        )
        with self.ws.session() as conn:
            conn.execute(
                "INSERT INTO approvals VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (short_ref(approval_id), plan_id, snap.plan_hash, draft_id, revision, snap.dataset_id,
                 snap.dataset_version, "active", snap.model_dump_json(), approved_by, snap.approved_at),
            )
        path = self.ws.plan_dir(plan_id) / "approved_snapshot.yaml"
        path.write_text(yaml.safe_dump(snap.model_dump(mode="json"), allow_unicode=True, sort_keys=False), encoding="utf-8")
        return ApprovalReceipt(plan_id=plan_id, approval_id=approval_id, plan_hash=snap.plan_hash)

    # S4 확인·승인 -------------------------------------------------------------
    _STEP_PREFIX = (("user_goal", "goal"), ("dataset_id", "goal"), ("dataset", "goal"), ("task", "goal"), ("modality", "goal"),
                    ("preprocessing", "preprocess"), ("excluded_steps", "preprocess"),
                    ("model", "train"), ("train_budget", "train"), ("split_policy", "train"), ("evaluation", "train"))

    @classmethod
    def issue_step(cls, path: str) -> str | None:
        """검증 항목을 고칠 화면(S1 goal / S2 preprocess / S3 train)."""
        for prefix, step in cls._STEP_PREFIX:
            if path == prefix or path.startswith(prefix + ".") or path.startswith(prefix + "["):
                return step
        return None

    def _approval_state(self, draft_id: str, plan_hash: str | None) -> dict[str, Any]:
        """화면용 승인 상태. 승인 토큰(approval_id)은 해시로만 저장되어 여기서 나오지 않는다."""
        with self.ws.session() as conn:
            active = conn.execute(
                "SELECT plan_id, plan_hash, approved_at, snapshot_json FROM approvals WHERE draft_id=? AND status='active' "
                "ORDER BY approved_at DESC LIMIT 1", (draft_id,)).fetchone()
            earlier = conn.execute(
                "SELECT 1 FROM approvals WHERE draft_id=? AND status IN ('invalidated','consumed') LIMIT 1",
                (draft_id,)).fetchone()
        if (active is not None and plan_hash is not None and active["plan_hash"] == plan_hash
                and not plugin_code_changed(ApprovedSnapshot.model_validate_json(active["snapshot_json"]), self.registry)):
            return {"status": "approved", "plan_id": active["plan_id"], "plan_hash": active["plan_hash"],
                    "approved_at": active["approved_at"]}
        with self.ws.session() as conn:  # 이 설정으로 이미 학습을 시작한 승인(consumed)
            used = conn.execute(
                "SELECT a.plan_id, a.plan_hash, a.approved_at, r.run_id FROM approvals a JOIN runs r "
                "ON r.approval_ref = a.approval_ref WHERE a.draft_id=? AND a.status='consumed' "
                "ORDER BY r.created_at DESC LIMIT 1", (draft_id,)).fetchone()
        if active is None and used is not None and plan_hash is not None and used["plan_hash"] == plan_hash:
            return {"status": "submitted", "plan_id": used["plan_id"], "plan_hash": used["plan_hash"],
                    "approved_at": used["approved_at"], "run_id": used["run_id"]}
        if active is not None or earlier is not None:
            return {"status": "invalidated"}
        return {"status": "none"}

    def confirmation(self, dataset_id: str) -> dict[str, Any]:
        """S4 요약용: 저장된 초안 + 최종 검증 + 승인 상태. 초안·승인을 바꾸지 않는다."""
        draft_id, draft, rev = self._require_plan(dataset_id)
        ds = self.datasets.get(dataset_id)
        result, _ = self.validator.validate(draft, mode="final", dataset=ds)
        approval = self._approval_state(draft_id, result.plan_hash if result.ok else None)
        return {"draft_id": draft_id, "draft": draft, "revision": rev, "dataset": ds,
                "issues": result.issues, "approval": approval}

    def approve_for_dataset(self, dataset_id: str, *, expected_revision: int, confirmed: bool,
                            approved_by: str = "local-user") -> dict[str, Any]:
        """사용자의 명시적 확인 + 예상 리비전으로 기존 approve()를 호출한다.

        돌려주는 값에 승인 토큰(approval_id)을 넣지 않는다(화면·로그 비노출). 같은 실행 설정이 이미
        승인되어 있으면 새 승인을 만들지 않고 그대로 돌려준다. 실행 기록(run)은 만들지 않는다."""
        if not confirmed:
            raise ApprovalError("설정을 확인했다는 체크가 필요합니다", code="CONFIRMATION_REQUIRED")
        draft_id, _, rev = self._require_plan(dataset_id)
        if expected_revision != rev:
            raise StateTransitionError("다른 곳에서 초안이 수정되었습니다. 최신 상태를 다시 불러오세요",
                                       code="REVISION_CONFLICT")
        view = self.confirmation(dataset_id)
        if view["approval"]["status"] == "approved":
            return {**view["approval"], "created": False}
        # 해시가 맞지 않는 이전 승인(데이터셋·플러그인 버전 변경 등)은 새 승인 전에 무효화한다.
        with self.ws.session() as conn:
            conn.execute("UPDATE approvals SET status='invalidated' WHERE draft_id=? AND status='active'", (draft_id,))
        receipt = self.approve(draft_id, rev, approved_by=approved_by)
        state = self._approval_state(draft_id, receipt["plan_hash"])
        return {**state, "created": True}

    def get_snapshot(self, plan_id: str) -> ApprovedSnapshot:
        with self.ws.session() as conn:
            row = conn.execute("SELECT snapshot_json FROM approvals WHERE plan_id=?", (plan_id,)).fetchone()
        if row is None:
            raise NotFound(f"확정된 계획이 없습니다: {plan_id}")
        return ApprovedSnapshot.model_validate_json(row["snapshot_json"])


class JobService:
    def __init__(self, ws: Workspace, registry: PluginRegistry, datasets: DatasetService, plans: PlanService):
        self.ws, self.registry, self.datasets, self.plans = ws, registry, datasets, plans

    # 제출 (CONTRACTS §5 experiment.submit) ------------------------------------
    def submit(self, *, plan_id: str, approval_id: str, idempotency_key: str | None = None) -> dict[str, Any]:
        """토큰 기반 제출(MCP experiment.submit). 화면 경로는 submit_approved()."""
        if not approval_id or not plan_id:
            raise SubmitRejected("승인 없이 실행할 수 없습니다", code="APPROVAL_REQUIRED")
        ref = short_ref(approval_id)
        return self._submit(plan_id=plan_id, ref=ref, key=idempotency_key or f"approval:{ref}")

    def submit_approved(self, *, dataset_id: str, plan_id: str, plan_hash: str,
                        idempotency_key: str | None = None) -> dict[str, Any]:
        """서버 전용 제출(S4 학습 시작). 승인 토큰은 해시로만 저장되어 있어 복구할 수 없으므로,
        화면이 보낸 계획 ID·해시를 승인 기록과 대조해 그 승인 기록으로 제출한다.
        이후 검증·중복 방지·트랜잭션은 토큰 기반 제출과 같다(_submit)."""
        if not plan_id or not plan_hash:
            raise SubmitRejected("승인 없이 실행할 수 없습니다", code="APPROVAL_REQUIRED")
        with self.ws.session() as conn:
            appr = conn.execute("SELECT approval_ref, plan_hash, dataset_id FROM approvals WHERE plan_id=?",
                                (plan_id,)).fetchone()
        if appr is None or appr["dataset_id"] != dataset_id:
            raise SubmitRejected("승인 기록이 없거나 계획과 일치하지 않습니다", code="APPROVAL_NOT_FOUND")
        if appr["plan_hash"] != plan_hash:
            raise SubmitRejected("승인한 설정과 요청한 설정이 다릅니다. 확인·실행 화면을 다시 불러오세요",
                                 code="PLAN_HASH_MISMATCH")
        ref = appr["approval_ref"]
        return self._submit(plan_id=plan_id, ref=ref, key=idempotency_key or f"approval:{ref}")

    def _submit(self, *, plan_id: str, ref: str, key: str) -> dict[str, Any]:
        conn = self.ws.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            appr = conn.execute("SELECT * FROM approvals WHERE approval_ref=?", (ref,)).fetchone()
            if appr is None or appr["plan_id"] != plan_id:
                raise SubmitRejected("승인 기록이 없거나 계획과 일치하지 않습니다", code="APPROVAL_NOT_FOUND")
            existing = conn.execute("SELECT run_id, state FROM runs WHERE approval_ref=?", (ref,)).fetchone()
            if existing is not None:  # 같은 유효 요청 재전송 → 기존 실행 반환
                conn.execute("COMMIT")
                return {"run_id": existing["run_id"], "state": existing["state"], "duplicate": True}
            if conn.execute("SELECT 1 FROM runs WHERE idempotency_key=?", (key,)).fetchone():
                raise SubmitRejected("다른 승인에 이미 사용된 요청 키입니다", code="IDEMPOTENCY_KEY_REUSED")
            if appr["status"] != "active":
                raise SubmitRejected("설정이 바뀌어 이전 확정이 무효화되었습니다. 다시 확정하세요", code="APPROVAL_INVALIDATED")

            snap = ApprovedSnapshot.model_validate_json(appr["snapshot_json"])
            if sha256_of(snap.immutable_config) != snap.plan_hash or snap.plan_hash != appr["plan_hash"]:
                raise SubmitRejected("확정된 설정이 변조되었습니다", code="PLAN_HASH_MISMATCH")
            latest = conn.execute(
                "SELECT exec_hash FROM drafts WHERE draft_id=? ORDER BY revision DESC LIMIT 1", (snap.draft_id,)
            ).fetchone()
            approved_rev = conn.execute(
                "SELECT exec_hash FROM drafts WHERE draft_id=? AND revision=?", (snap.draft_id, snap.draft_revision)
            ).fetchone()
            if latest is None or approved_rev is None or latest["exec_hash"] != approved_rev["exec_hash"]:
                raise SubmitRejected("확정 이후 실행 설정이 바뀌었습니다. 다시 확정하세요", code="APPROVAL_INVALIDATED")
            ds = self.datasets.find(snap.dataset_id)
            if ds is None or ds.version != snap.dataset_version or ds.state != DatasetState.PROFILED:
                raise SubmitRejected("확정 이후 데이터셋이 바뀌었습니다(버전 불일치). 다시 분석·확정하세요", code="DATASET_CHANGED")
            for key_ in snap.plugin_versions:
                pid, ver = key_.split("@")
                spec = self.registry.get(pid, ver)
                if spec is None or not spec.ready:
                    raise SubmitRejected(f"확정된 플러그인을 실행할 수 없습니다: {key_}", code="PLUGIN_UNAVAILABLE")
            if changed := plugin_code_changed(snap, self.registry):
                raise SubmitRejected(f"확정 이후 사용자 플러그인 코드가 바뀌었습니다: {changed}. "
                                     "앱을 다시 시작해 새 코드를 불러온 뒤 다시 확정하세요", code="PLUGIN_CODE_CHANGED")

            run_id = _new_id("run")
            rec = RunRecord(
                run_id=run_id, plan_id=plan_id, plan_hash=snap.plan_hash, approval_id_ref=ref,
                dataset_id=snap.dataset_id, dataset_version=snap.dataset_version,
                state=RunState.QUEUED, created_at=utcnow_iso(), idempotency_key=key,
            )
            conn.execute(
                "INSERT INTO runs VALUES (?,?,?,?,?,?,?)",
                (run_id, plan_id, ref, key, rec.model_dump_json(), rec.state.value, rec.created_at),
            )
            ev = RunEvent(run_id=run_id, seq=1, state=RunState.QUEUED, stage="queued", message_code="RUN_QUEUED")
            conn.execute("INSERT INTO run_events VALUES (?,?,?)", (run_id, 1, ev.model_dump_json()))
            conn.execute("UPDATE approvals SET status='consumed' WHERE approval_ref=?", (ref,))
            conn.execute("COMMIT")
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()
        exp = self.ws.root / "experiments" / run_id
        exp.mkdir(parents=True, exist_ok=True)
        (exp / "config.yaml").write_text(
            yaml.safe_dump(snap.immutable_config, allow_unicode=True, sort_keys=False), encoding="utf-8"
        )
        return {"run_id": run_id, "state": RunState.QUEUED.value, "duplicate": False}

    # 이벤트·상태 (D-04) ------------------------------------------------------
    def append_event(self, run_id: str, *, state: RunState, stage: RunStage, message_code: str,
                     progress: float | None = None) -> RunEvent:
        """실행기(worker)가 호출하는 내부 인터페이스. P0에서는 테스트가 호출한다."""
        conn = self.ws.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            run = conn.execute("SELECT state FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if run is None:
                raise NotFound(f"실행을 찾을 수 없습니다: {run_id}")
            cur = RunState(run["state"])
            if state not in RUN_TRANSITIONS[cur]:
                raise StateTransitionError(f"실행 상태를 {cur.value} → {state.value}로 바꿀 수 없습니다")
            last = conn.execute("SELECT COALESCE(MAX(seq),0) AS s FROM run_events WHERE run_id=?", (run_id,)).fetchone()["s"]
            ev = RunEvent(run_id=run_id, seq=last + 1, state=state, stage=stage, progress=progress, message_code=message_code)
            conn.execute("INSERT INTO run_events VALUES (?,?,?)", (run_id, ev.seq, ev.model_dump_json()))
            conn.execute("UPDATE runs SET state=? WHERE run_id=?", (state.value, run_id))
            conn.execute("COMMIT")
            return ev
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def get_status(self, run_id: str, *, after_seq: int = 0) -> RunStatus:
        with self.ws.session() as conn:
            rows = conn.execute("SELECT event_json FROM run_events WHERE run_id=? ORDER BY seq", (run_id,)).fetchall()
        if not rows:
            raise NotFound(f"실행을 찾을 수 없습니다: {run_id}")
        events = [RunEvent.model_validate_json(r["event_json"]) for r in rows]
        last = events[-1]
        return RunStatus(run_id=run_id, state=last.state, stage=last.stage, last_seq=last.seq,
                         events=[e for e in events if e.seq > after_seq])

    def count_runs(self) -> int:
        with self.ws.session() as conn:
            return conn.execute("SELECT COUNT(*) AS n FROM runs").fetchone()["n"]

    def get_record(self, run_id: str) -> RunRecord:
        with self.ws.session() as conn:
            row = conn.execute("SELECT record_json, state FROM runs WHERE run_id=?", (run_id,)).fetchone()
        if row is None:
            raise NotFound(f"실행을 찾을 수 없습니다: {run_id}")
        rec = RunRecord.model_validate_json(row["record_json"])
        return rec.model_copy(update={"state": RunState(row["state"])})

    @staticmethod
    def clean_name(name: str | None) -> str | None:
        """실행 이름(ADD-UX-06). 앞뒤 공백 제거, 빈 값은 이름 없음, 줄바꿈·제어 문자 불가, 60자 이하."""
        text = (name or "").strip()
        if any(ord(ch) < 32 for ch in text):
            raise WorkbenchError("이름에 줄바꿈이나 제어 문자를 넣을 수 없습니다", code="NAME_INVALID")
        if len(text) > 60:
            raise WorkbenchError("이름은 60자 이하로 입력해 주세요", code="NAME_TOO_LONG")
        return text or None

    def rename(self, run_id: str, name: str | None) -> RunRecord:
        return self.update_record(run_id, name=self.clean_name(name))

    def update_record(self, run_id: str, **fields: Any) -> RunRecord:
        conn = self.ws.connect()
        try:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT record_json FROM runs WHERE run_id=?", (run_id,)).fetchone()
            if row is None:
                raise NotFound(f"실행을 찾을 수 없습니다: {run_id}")
            rec = RunRecord.model_validate_json(row["record_json"]).model_copy(update=fields)
            RunRecord.model_validate(rec.model_dump())  # 필드 형식 확인
            conn.execute("UPDATE runs SET record_json=? WHERE run_id=?", (rec.model_dump_json(), run_id))
            conn.execute("COMMIT")
            return rec
        except Exception:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
        finally:
            conn.close()

    def list_runs(self, dataset_id: str | None = None) -> list[RunRecord]:
        with self.ws.session() as conn:
            rows = conn.execute("SELECT record_json, state FROM runs ORDER BY created_at DESC, rowid DESC").fetchall()
        out = [RunRecord.model_validate_json(r["record_json"]).model_copy(update={"state": RunState(r["state"])})
               for r in rows]
        return [r for r in out if dataset_id is None or r.dataset_id == dataset_id]

    def finish(self, run_id: str, state: RunState, *, stage: RunStage, message_code: str,
               error_code: str | None = None, error_message_ko: str | None = None) -> bool:
        """종료 상태로 바꾼다. 이미 종료되었으면 아무것도 하지 않고 False."""
        status = self.get_status(run_id)
        if status.state in TERMINAL_RUN_STATES:
            return False
        if state == RunState.FAILED or state == RunState.CANCELED:
            self.update_record(run_id, finished_at=utcnow_iso(), error_code=error_code, error_message_ko=error_message_ko)
        try:
            self.append_event(run_id, state=state, stage=stage, message_code=message_code)
        except StateTransitionError:
            return False
        return True

    # 취소 (CONTRACTS §5 experiment.cancel) ------------------------------------
    def request_cancel_confirmation(self, run_id: str) -> str:
        """UI가 확인 대화상자를 띄운 뒤 받는 1회용 확인 ID."""
        self.get_status(run_id)
        token = "confirm_" + secrets.token_urlsafe(16)
        with self.ws.session() as conn:
            conn.execute("INSERT INTO confirmations VALUES (?,?,?,0)", (short_ref(token), run_id, "cancel"))
        return token

    def cancel(self, run_id: str, confirmation_id: str) -> dict[str, Any]:
        with self.ws.session() as conn:
            row = conn.execute(
                "SELECT * FROM confirmations WHERE confirmation_ref=? AND run_id=? AND action='cancel'",
                (short_ref(confirmation_id or ""), run_id),
            ).fetchone()
            if row is None or row["used"]:
                raise SubmitRejected("취소 확인이 필요합니다", code="CONFIRMATION_REQUIRED")
            conn.execute("UPDATE confirmations SET used=1 WHERE confirmation_ref=?", (row["confirmation_ref"],))
        status = self.get_status(run_id)
        if status.state in TERMINAL_RUN_STATES:
            raise SubmitRejected(f"이미 종료된 실행입니다 ({status.state.value})", code="RUN_FINISHED")
        if status.state == RunState.CANCEL_REQUESTED:
            return {"cancel_requested": True, "state": status.state.value}
        ev = self.append_event(run_id, state=RunState.CANCEL_REQUESTED, stage=status.stage, message_code="CANCEL_REQUESTED")
        return {"cancel_requested": True, "state": ev.state.value}


def dumps(obj: Any) -> str:
    return json.dumps(obj, ensure_ascii=False, indent=2, default=str)
