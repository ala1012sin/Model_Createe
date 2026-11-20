"""사용자 정의 전처리 플러그인 발견·검증·등록 (P2, docs/P2_PLUGIN_BRIEF.md).

- 위치: 환경변수 MLWB_PLUGIN_DIRS(os.pathsep 구분, 빈 값이면 없음)가 있으면 그것만, 없으면 <프로젝트>/plugins.
- 폴더마다 plugin.json(PluginSpec 필드 + entry "파일.py:클래스")을 먼저 검증한 뒤에만 코드를 불러온다.
- 신뢰 모델: 사용자가 직접 둔 로컬 코드만 실행한다(샌드박스 아님). zip·원격 다운로드·자동 설치는 하지 않는다.
- 문제 안내에는 폴더 이름만 쓰고 절대경로는 넣지 않는다(LLM 카탈로그에도 사유가 실리므로).
"""

from __future__ import annotations

import hashlib
import importlib.util
import json
import math
import os
import sys
from pathlib import Path
from typing import Any

import pydantic

from mlworkbench.core.contracts import PluginSpec
from mlworkbench.core.errors import RegistryError

ID_PREFIX = "ts.custom."
MANIFEST = "plugin.json"
MAX_MANIFEST_BYTES = 256 * 1024
_FORBIDDEN_KEYS = {"status", "reason_unavailable", "origin", "code_sha256"}
_EXTRA_KEYS = {"entry"}


def _project_root() -> Path | None:
    import mlworkbench

    root = Path(mlworkbench.__file__).resolve().parents[2]
    return root if (root / "pyproject.toml").is_file() and (root / "src" / "mlworkbench").is_dir() else None


def plugin_dirs() -> list[Path]:
    env = os.environ.get("MLWB_PLUGIN_DIRS")
    if env is not None:
        return [Path(p).resolve() for p in env.split(os.pathsep) if p.strip()]
    root = _project_root()
    return [(root / "plugins").resolve()] if root else []


def code_hash(folder: Path) -> str:
    """plugin.json과 폴더 바로 아래 .py 파일 내용 해시(파일 이름 포함, 정렬)."""
    h = hashlib.sha256()
    files = sorted([folder / MANIFEST] + [p for p in folder.glob("*.py") if p.is_file()], key=lambda p: p.name)
    for p in files:
        h.update(p.name.encode("utf-8") + b"\0")
        h.update(p.read_bytes())
        h.update(b"\0")
    return h.hexdigest()


def _problem(folder: str, code: str, message_ko: str) -> dict[str, str]:
    return {"folder": folder, "code": code, "message_ko": message_ko}


def _parse_manifest(folder: Path) -> tuple[PluginSpec, str]:
    """(검증된 명세, entry). 실패 시 RegistryError(code, 사용자용 문구)."""
    f = folder / MANIFEST
    if f.is_symlink() or f.stat().st_size > MAX_MANIFEST_BYTES:
        raise RegistryError("plugin.json이 링크이거나 너무 큽니다", code="PLUGIN_MANIFEST_INVALID")
    try:
        raw = json.loads(f.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise RegistryError(f"plugin.json을 읽을 수 없습니다(JSON 형식): {type(e).__name__}", code="PLUGIN_MANIFEST_INVALID") from None
    if not isinstance(raw, dict):
        raise RegistryError("plugin.json의 최상위는 객체여야 합니다", code="PLUGIN_MANIFEST_INVALID")
    bad = sorted(_FORBIDDEN_KEYS & raw.keys())
    if bad:
        raise RegistryError(f"plugin.json에 직접 쓸 수 없는 항목입니다: {', '.join(bad)}", code="PLUGIN_MANIFEST_INVALID")
    entry = raw.pop("entry", None)
    if not isinstance(entry, str) or entry.count(":") != 1:
        raise RegistryError('entry는 "파일.py:클래스이름" 형식이어야 합니다', code="PLUGIN_MANIFEST_INVALID")
    try:
        spec = PluginSpec.model_validate({**raw, "status": "ready", "origin": "user"})
    except pydantic.ValidationError as e:
        where = ", ".join(sorted({".".join(str(x) for x in err["loc"]) for err in e.errors()}))
        raise RegistryError(f"plugin.json 항목이 계약과 맞지 않습니다: {where}", code="PLUGIN_MANIFEST_INVALID") from None
    if not spec.id.startswith(ID_PREFIX) or len(spec.id) <= len(ID_PREFIX):
        raise RegistryError(f"사용자 플러그인 ID는 '{ID_PREFIX}이름' 형식이어야 합니다: {spec.id}", code="PLUGIN_ID_NOT_ALLOWED")
    if (spec.kind, spec.modality) != ("preprocessor", "timeseries"):
        raise RegistryError("현재는 시계열 전처리(kind=preprocessor, modality=timeseries)만 지원합니다", code="PLUGIN_KIND_UNSUPPORTED")
    if (spec.input_data_kind, spec.output_data_kind) != ("table.timeseries", "table.timeseries"):
        raise RegistryError("입출력 형식은 table.timeseries여야 합니다", code="PLUGIN_MANIFEST_INVALID")
    if any(t != "forecasting" for t in spec.tasks):
        raise RegistryError("지원하는 태스크는 forecasting뿐입니다", code="PLUGIN_MANIFEST_INVALID")
    if spec.fit_scope is None or not (spec.source_library or "").strip():
        raise RegistryError("fit_scope(stateless/train_only)와 source_library를 지정해야 합니다", code="PLUGIN_MANIFEST_INVALID")
    if spec.fit_scope == "stateless" and not 30 <= spec.order.stage < 60:
        raise RegistryError("stateless 전처리의 order.stage는 30~59여야 합니다(세션 분리·리샘플 뒤, 스케일링 앞)",
                            code="PLUGIN_MANIFEST_INVALID")
    if spec.fit_scope == "train_only" and spec.order.stage < 60:
        raise RegistryError("train_only 전처리의 order.stage는 60 이상이어야 합니다", code="PLUGIN_MANIFEST_INVALID")
    from mlworkbench.core import schema_check

    if spec.parameter_schema.get("type") != "object" or (err := schema_check.schema_error(spec.parameter_schema)):
        raise RegistryError("parameter_schema가 올바른 JSON Schema(object)가 아닙니다", code="INVALID_PARAMETER_SCHEMA")
    if spec.order.locked:
        raise RegistryError("사용자 플러그인은 순서 고정(locked)을 쓸 수 없습니다", code="PLUGIN_MANIFEST_INVALID")
    return spec.model_copy(update={"train_only": spec.fit_scope == "train_only"}), entry


def _load_class(folder: Path, entry: str, digest: str):
    file_name, cls_name = entry.split(":")
    if Path(file_name).name != file_name or not file_name.endswith(".py") or not cls_name.isidentifier():
        raise RegistryError("entry 파일은 같은 폴더의 .py 파일, 클래스는 Python 이름이어야 합니다", code="PLUGIN_ENTRY_INVALID")
    path = folder / file_name
    if not path.is_file() or path.is_symlink() or path.resolve().parent != folder.resolve():
        raise RegistryError(f"entry 파일이 폴더에 없습니다: {file_name}", code="PLUGIN_ENTRY_INVALID")
    mod_name = f"mlwb_user_plugin_{digest[:16]}"
    try:
        mspec = importlib.util.spec_from_file_location(mod_name, path)
        module = importlib.util.module_from_spec(mspec)  # type: ignore[arg-type]
        sys.modules[mod_name] = module
        mspec.loader.exec_module(module)  # type: ignore[union-attr]
    except (Exception, SystemExit) as e:  # noqa: BLE001 - 사용자 코드의 어떤 오류도 서버를 멈추지 않게
        sys.modules.pop(mod_name, None)
        raise RegistryError(f"코드를 불러오지 못했습니다: {type(e).__name__}", code="PLUGIN_IMPORT_FAILED") from None
    cls = getattr(module, cls_name, None)
    if not isinstance(cls, type):
        raise RegistryError(f"클래스를 찾을 수 없습니다: {cls_name}", code="PLUGIN_ENTRY_INVALID")
    try:
        impl = cls()
    except Exception as e:  # noqa: BLE001
        raise RegistryError(f"클래스를 만들 수 없습니다: {type(e).__name__}", code="PLUGIN_IMPORT_FAILED") from None
    if not callable(getattr(impl, "transform", None)):
        raise RegistryError("transform 메서드가 없습니다", code="PLUGIN_METHOD_MISSING")
    return impl


def load_user_plugins(registry, dirs: list[Path] | None = None) -> list[dict[str, str]]:
    """dirs의 하위 폴더에서 사용자 플러그인을 등록한다. 반환: 불러오기 문제 목록."""
    from mlworkbench.plugins.user_guard import GuardedPreprocessor

    problems: list[dict[str, str]] = []
    for base in plugin_dirs() if dirs is None else dirs:
        if not base.is_dir():
            continue
        for folder in sorted(p for p in base.iterdir() if p.is_dir() and not p.name.startswith((".", "_"))):
            if not (folder / MANIFEST).is_file():
                continue
            name = folder.name
            try:
                spec, entry = _parse_manifest(folder)
                digest = code_hash(folder)
                spec = spec.model_copy(update={"code_sha256": digest})
            except RegistryError as e:
                problems.append(_problem(name, e.code, str(e)))
                continue
            except OSError:
                problems.append(_problem(name, "PLUGIN_MANIFEST_INVALID", "plugin.json을 읽을 수 없습니다"))
                continue
            if registry.has(spec.id) and spec.version in registry.versions(spec.id):
                problems.append(_problem(name, "DUPLICATE_PLUGIN", f"같은 ID·버전이 이미 등록되어 있습니다: {spec.key}"))
                continue
            registry._user_folders[spec.key] = folder
            from mlworkbench.core.registry import _module_available

            missing = [m for m in spec.dependencies if not m.isidentifier() or not _module_available(m)]
            if missing:  # 코드를 불러오기 전에 확인한다(설치는 하지 않음)
                registry.register(spec.model_copy(update={
                    "status": "missing_dependency",
                    "reason_unavailable": f"필요한 Python 모듈이 없습니다: {', '.join(missing)}"}))
                continue
            try:
                impl = _load_class(folder, entry, digest)
                registry.register_preprocessor(GuardedPreprocessor(spec, impl))
            except RegistryError as e:
                # 명세는 맞으니 목록에는 보이되 실행할 수 없게 둔다.
                try:
                    registry.register(spec.model_copy(update={"status": "invalid", "reason_unavailable": str(e)}))
                except RegistryError as e2:
                    problems.append(_problem(name, e2.code, str(e2)))
                    continue
                problems.append(_problem(name, e.code, str(e)))
    return problems


def is_finite_json(value: Any) -> bool:
    """train_only 상태가 JSON으로 저장·복원 가능한지(NaN·inf 제외)."""
    try:
        text = json.dumps(value, allow_nan=False)
    except (TypeError, ValueError):
        return False
    return json.loads(text) == value or _float_equal(json.loads(text), value)


def _float_equal(a: Any, b: Any) -> bool:
    if isinstance(a, float) and isinstance(b, float):
        return math.isclose(a, b)
    if isinstance(a, list) and isinstance(b, (list, tuple)):
        return len(a) == len(b) and all(_float_equal(x, y) for x, y in zip(a, b))
    if isinstance(a, dict) and isinstance(b, dict):
        return a.keys() == b.keys() and all(_float_equal(a[k], b[k]) for k in a)
    return a == b
