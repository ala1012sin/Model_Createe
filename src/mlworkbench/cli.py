"""mlwb CLI — doctor, plugins list, providers list, plan validate.

출력에는 비밀키 값·절대경로를 쓰지 않는다(환경변수 이름과 설정 여부만).
종료 코드: 0 정상, 1 검증 실패/점검 실패, 2 사용법·설정 오류.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import logging
import os
import platform
import sqlite3
import sys
from pathlib import Path
from typing import Any

import yaml

from mlworkbench import SCHEMA_VERSION, __version__
from mlworkbench.core.contracts import DatasetManifest
from mlworkbench.core.errors import ConfigError, WorkbenchError
from mlworkbench.core.registry import PluginRegistry, default_registry
from mlworkbench.core.security import RedactingFilter, redact, secret_values_from_env
from mlworkbench.core.validation import PlanValidator
from mlworkbench.providers.config import ProvidersFile, load_providers

DEFAULT_PROVIDERS = "configs/providers.yaml"


def _providers_path(arg: str | None) -> Path:
    return Path(arg or os.environ.get("MLWB_PROVIDERS") or DEFAULT_PROVIDERS)


def _setup_logging(env_names: list[str]) -> None:
    handler = logging.StreamHandler(sys.stderr)
    handler.addFilter(RedactingFilter(env_names))
    logging.basicConfig(level=logging.WARNING, handlers=[handler], force=True)


def _emit(obj: Any, as_json: bool, text: str, env_names: list[str]) -> None:
    out = json.dumps(obj, ensure_ascii=False, indent=2) if as_json else text
    print(redact(out, secret_values_from_env(env_names)))


def _load_file(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        raise ConfigError(f"파일이 없습니다: {p.name}")
    text = p.read_text(encoding="utf-8")
    data = json.loads(text) if p.suffix.lower() == ".json" else yaml.safe_load(text)
    if not isinstance(data, dict):
        raise ConfigError("계획 파일 최상위는 객체여야 합니다")
    return data


# --------------------------------------------------------------------------
def cmd_doctor(args: argparse.Namespace) -> int:
    from mlworkbench.storage.database import Workspace

    checks: list[dict[str, str]] = []

    def add(name: str, status: str, detail: str) -> None:
        checks.append({"check": name, "status": status, "detail": detail})

    py = sys.version_info
    add("python", "ok" if py >= (3, 10) else "fail", f"{platform.python_implementation()} {py.major}.{py.minor}.{py.micro}")
    for mod in ("pydantic", "yaml"):
        spec = importlib.util.find_spec(mod)
        add(f"package:{mod}", "ok" if spec else "fail", "설치됨" if spec else "없음 — pip install -e .")
    import pydantic

    add("pydantic>=2", "ok" if pydantic.VERSION.startswith("2.") else "fail", pydantic.VERSION)
    add("sqlite", "ok", sqlite3.sqlite_version)
    for mod, phase in (("fastapi", "P1 웹 API"), ("uvicorn", "P1 웹 서버"), ("xgboost", "P2"), ("openpyxl", "P2 엑셀"),
                       ("torch", "P3")):
        found = importlib.util.find_spec(mod) is not None
        hint = " — pip install -e \".[web]\"" if (not found and phase.startswith("P1")) else ""
        add(f"optional:{mod}", "ok" if found else "info", ("설치됨" if found else "미설치") + f" ({phase}에서 필요){hint}")

    try:
        ws = Workspace(args.workspace).init()
        add("workspace", "ok", f"초기화 완료: {ws.root.name}/ (datasets, plans, experiments, mlwb.sqlite)")
    except OSError as e:
        add("workspace", "fail", f"쓰기 불가: {e.strerror}")
    except sqlite3.Error as e:
        add("workspace", "fail", f"SQLite 초기화 실패: {e} — 워크스페이스를 로컬 디스크로 지정하세요(--workspace 또는 MLWB_WORKSPACE)")

    reg = default_registry()
    specs = reg.all()
    ready = sum(s.ready for s in specs)
    add("registry", "ok", f"플러그인 {len(specs)}개 등록 · 실행 가능 {ready}개 · 미구현 {len(specs) - ready}개")
    users = [s for s in specs if s.origin == "user"]
    if users or reg.load_problems:
        add("user_plugins", "ok" if not reg.load_problems else "info",
            f"사용자 정의 {len(users)}개(실행 가능 {sum(s.ready for s in users)}개)"
            + "".join(f" · {s.key}: {s.reason_unavailable}" for s in users if not s.ready))
    for pr in reg.load_problems:
        add(f"plugin:{pr['folder']}", "info", f"불러오지 않음 [{pr['code']}] {pr['message_ko']}")

    env_names: list[str] = []
    try:
        pf = load_providers(_providers_path(args.providers))
        env_names = pf.env_names()
        act = pf.active
        add("providers", "ok", f"활성: {act.id} ({act.kind}) · 외부 전송 허용: {pf.policy.allow_external_data}")
        for name in env_names:
            add(f"env:{name}", "ok" if os.environ.get(name) else "info", "설정됨" if os.environ.get(name) else "미설정")
        if act.is_external and not pf.policy.allow_external_data:
            add("policy", "info", "외부 공급자 사용 시 전송 요약 확인·허용이 필요합니다")
    except ConfigError as e:
        add("providers", "fail", e.message_ko)

    failed = any(c["status"] == "fail" for c in checks)
    text = "\n".join(f"[{c['status']:>4}] {c['check']:<20} {c['detail']}" for c in checks)
    text = f"mlwb {__version__} · schema {SCHEMA_VERSION}\n{text}\n결과: {'실패' if failed else '정상'}"
    _emit({"version": __version__, "checks": checks, "ok": not failed}, args.json, text, env_names)
    return 1 if failed else 0


def cmd_plugins_list(args: argparse.Namespace) -> int:
    reg = default_registry()
    specs = reg.find(kind=args.kind, modality=args.modality, task=args.task, ready_only=args.ready_only)
    rows = [s.public_view() for s in specs]
    lines = [f"{'상태':<6} {'종류':<12} {'id@version':<40} 이름 · 사유"]
    for s in specs:
        state = "실행가능" if s.ready else "불가"
        lines.append(f"{state:<6} {s.kind:<12} {s.key:<40} {s.display_name_ko}"
                     + ("" if s.ready else f" · {s.reason_unavailable or s.status}"))
    lines.append(f"카탈로그 {reg.catalog_version()} · {len(specs)}개")
    _emit({"catalog_version": reg.catalog_version(), "items": rows}, args.json, "\n".join(lines), [])
    return 0


def cmd_providers_list(args: argparse.Namespace) -> int:
    pf: ProvidersFile = load_providers(_providers_path(args.providers))
    env_names = pf.env_names()
    items = []
    for pid, p in pf.profiles.items():
        items.append({
            "id": pid, "active": pid == pf.active_provider, "kind": p.kind, "model": p.model,
            "base_url": p.base_url, "external": p.is_external, "auth_mode": p.auth_mode,
            "api_key_env": p.api_key_env, "api_key_present": p.key_present() if p.api_key_env else None,
        })
    lines = []
    for it in items:
        key = "" if not it["api_key_env"] else f" · 키 {it['api_key_env']}({'설정됨' if it['api_key_present'] else '미설정'})"
        lines.append(f"{'*' if it['active'] else ' '} {it['id']:<16} {it['kind']:<18} {it['model']:<28}"
                     f" {'외부' if it['external'] else '로컬'}{key}")
    lines.append(f"정책: 외부 전송 허용={pf.policy.allow_external_data}, 원본 파일·절대경로·픽셀 전송=항상 금지")
    _emit({"active_provider": pf.active_provider, "profiles": items, "policy": pf.policy.model_dump()},
          args.json, "\n".join(lines), env_names)
    return 0


def cmd_plan_validate(args: argparse.Namespace) -> int:
    plan = _load_file(args.file)
    reg = default_registry()
    notes = []
    if args.assume_ready:
        reg = PluginRegistry(s.model_copy(update={"status": "ready"}) for s in reg.all())
        notes.append("가정: 모든 등록 플러그인을 실행 가능으로 간주(구조 검증 전용, 실제 실행 불가)")
    dataset = DatasetManifest.model_validate(_load_file(args.dataset)) if args.dataset else None
    result, exe = PlanValidator(reg).validate(plan, mode=args.mode, dataset=dataset)
    payload = {"ok": result.ok, "mode": result.mode, "plan_hash": result.plan_hash,
               "issues": [i.model_dump() for i in result.issues], "notes": notes}
    icon = {"error": "✕", "warning": "⚠", "info": "ⓘ"}
    lines = [f"! {n}" for n in notes]
    lines += [f"{icon[i.severity]} [{i.code}] {i.path}: {i.message_ko}" + (f" → {i.fix_hint_ko}" if i.fix_hint_ko else "")
              for i in result.issues]
    summary = f"검증({args.mode}): {'통과' if result.ok else '실패'} · 오류 {len(result.errors)} · 경고 {len(result.warnings)}"
    if result.plan_hash:
        summary += f"\nplan_hash: {result.plan_hash} (승인 권한 아님)"
    lines.append(summary)
    _emit(payload, args.json, "\n".join(lines), [])
    return 0 if result.ok else 1


# --- datasets (P1 CSV) -----------------------------------------------------
_STATE_KO = {"UPLOADING": "업로드 중", "REGISTERED": "등록됨", "PROFILING": "분석 중", "PROFILED": "완료",
             "UPLOAD_FAILED": "업로드 실패", "PROFILE_FAILED": "분석 실패"}


def _dataset_service(args: argparse.Namespace):
    from mlworkbench.app.services import DatasetService
    from mlworkbench.storage.database import Workspace

    return DatasetService(Workspace(args.workspace).init())


def _manifest_line(m) -> str:
    line = f"{m.dataset_id}  {_STATE_KO[m.state.value]:<6}  {m.name}"
    if m.row_count is not None:
        line += f"  · 행 {m.row_count:,} · 열 {len(m.columns)}"
    if m.error:
        line += f"\n    ✕ [{m.error.code}] {m.error.message_ko}"
    return line


def cmd_experiment_verify(args: argparse.Namespace) -> int:
    """완료된 실행의 승인 설정을 같은 seed로 임시 폴더에 다시 계산해 지표를 비교한다.

    새 실행 기록을 만들지 않고 원래 실행 폴더를 바꾸지 않는다(재현성 확인 전용).
    저장된 예측값으로 다시 계산한 지표가 metrics.json과 같은지도 확인한다."""
    import tempfile

    import pandas as pd

    from mlworkbench.app.services import DatasetService, JobService, PlanService
    from mlworkbench.pipelines import metrics as M
    from mlworkbench.pipelines.timeseries import run_timeseries
    from mlworkbench.storage.database import Workspace

    ws = Workspace(args.workspace)
    reg = default_registry()
    datasets = DatasetService(ws)
    plans = PlanService(ws, reg, datasets)
    jobs = JobService(ws, reg, datasets, plans)
    rec = jobs.get_record(args.run_id)
    if rec.state.value != "SUCCEEDED":
        print(f"완료된 실행만 확인할 수 있습니다 (현재 {rec.state.value})", file=sys.stderr)
        return 1
    exp = ws.root / "experiments" / args.run_id
    saved = json.loads((exp / "metrics.json").read_text(encoding="utf-8"))
    env = json.loads((exp / "environment.json").read_text(encoding="utf-8"))
    snap = plans.get_snapshot(rec.plan_id)
    from mlworkbench.app.services import plugin_code_changed

    if changed := plugin_code_changed(snap, reg):
        print(f"승인 이후 사용자 플러그인 코드가 바뀌어 같은 계산을 재현할 수 없습니다: {changed}", file=sys.stderr)
        return 1
    prof = datasets.get_local_profile(rec.dataset_id)
    with tempfile.TemporaryDirectory(prefix="mlwb-verify-") as tmp:
        ds = datasets.get(rec.dataset_id)
        again = run_timeseries(snap.immutable_config, csv_path=ws.root / (ds.local_path or ""),
                               csv_options={"encoding": prof.encoding, "delimiter": prof.delimiter},
                               numeric_cols=[c.name for c in prof.columns if c.dtype == "numeric"],
                               registry=reg, out_dir=Path(tmp), seed=int(env["seed"]))["metrics"]
    names = list(snap.immutable_config["evaluation"]["metrics"])
    pred = pd.read_csv(exp / "predictions" / "predictions.csv")
    rows, worst = [], 0.0
    for sp in ("val", "test"):
        if sp not in saved:
            continue
        part = pred[pred["split"] == sp]
        recomputed = M.compute(part["actual"], part["predicted"], names)
        for k in names:
            a, b, c = saved[sp]["overall"]["model"][k], again[sp]["overall"]["model"][k], recomputed[k]
            d_rerun = abs(a - b) if a is not None and b is not None else 0.0
            d_pred = abs(a - c) if a is not None and c is not None else 0.0
            worst = max(worst, d_rerun, d_pred)
            rows.append({"split": sp, "metric": k, "saved": a, "rerun": b, "abs_diff_rerun": d_rerun,
                         "from_predictions": c, "abs_diff_predictions": d_pred})
    ok = worst <= args.tolerance
    out = {"run_id": args.run_id, "seed": env["seed"], "tolerance": args.tolerance, "ok": ok, "max_abs_diff": worst,
           "rows": rows}
    if args.json:
        print(json.dumps(out, ensure_ascii=False, indent=2))
    else:
        for r in rows:
            print(f"{r['split']:4} {r['metric']:5} 저장 {r['saved']:.6f}  재실행 {r['rerun']:.6f} (차이 {r['abs_diff_rerun']:.2e})"
                  f"  예측값 재계산 {r['from_predictions']:.6f} (차이 {r['abs_diff_predictions']:.2e})")
        print(f"허용 오차 {args.tolerance:g} → {'일치' if ok else '불일치'} (최대 차이 {worst:.2e})")
    return 0 if ok else 1


def cmd_serve(args: argparse.Namespace) -> int:
    import ipaddress

    try:
        import uvicorn

        from mlworkbench.api import create_app
    except ImportError:
        print('오류: 웹 의존성이 없습니다. pip install -e ".[web]"', file=sys.stderr)
        return 2
    host = args.host
    loopback = host == "localhost" or (host.replace(".", "").isdigit() and ipaddress.ip_address(host).is_loopback)
    if not loopback:
        print("오류: 이 앱은 로컬 전용입니다. 127.0.0.1 또는 localhost로 실행하세요", file=sys.stderr)
        return 2
    print(f"ML Workbench: http://{host}:{args.port}  (종료: Ctrl+C)")
    uvicorn.run(create_app(args.workspace), host=host, port=args.port, log_level="warning")
    return 0


def cmd_datasets_add(args: argparse.Namespace) -> int:
    svc = _dataset_service(args)
    fn = svc.register_csv if args.no_profile else svc.register_and_profile
    m = fn(args.file, retry_of=args.retry_of)
    _emit(m.model_dump(mode="json", exclude={"local_path"}), args.json, _manifest_line(m), [])
    return 0 if m.state.value in ("PROFILED", "REGISTERED") else 1


def cmd_datasets_profile(args: argparse.Namespace) -> int:
    m = _dataset_service(args).profile(args.dataset_id)
    _emit(m.model_dump(mode="json", exclude={"local_path"}), args.json, _manifest_line(m), [])
    return 0 if m.state.value == "PROFILED" else 1


def cmd_datasets_list(args: argparse.Namespace) -> int:
    items = _dataset_service(args).list()
    text = "\n".join(_manifest_line(m) for m in items) or "등록된 데이터셋이 없습니다"
    _emit([m.model_dump(mode="json", exclude={"local_path"}) for m in items], args.json, text, [])
    return 0


def cmd_datasets_show(args: argparse.Namespace) -> int:
    svc = _dataset_service(args)
    m = svc.get(args.dataset_id)
    if args.safe:
        sp = svc.get_safe_profile(args.dataset_id)
        print(json.dumps(sp.model_dump(mode="json"), ensure_ascii=False, indent=2))
        return 0
    lp = svc.get_local_profile(args.dataset_id)
    if lp is None:
        _emit(m.model_dump(mode="json", exclude={"local_path"}), args.json, _manifest_line(m), [])
        return 0 if m.state.value != "PROFILE_FAILED" else 1
    lines = [_manifest_line(m), f"인코딩 {lp.encoding} · 구분자 {lp.delimiter!r} · 중복 행 {lp.duplicate_rows} · 제외 행 {lp.malformed_rows}",
             f"{'별칭':<10} {'이름':<20} {'타입':<12} {'결측':>7} {'고유값':>8}"]
    for c in lp.columns:
        lines.append(f"{c.alias:<10} {c.name:<20} {c.dtype:<12} {c.null_pct:>7.2%} {c.distinct_count:>8,}")
    for t in lp.time_candidates:
        iv = t.interval
        lines.append(f"⏱ 시간열 후보 {t.name}({t.alias}) 해석률 {t.parse_rate:.1%} · 간격 "
                     + (f"{iv.label} ({iv.share:.1%})" if iv.status == "estimated" else f"추정 불가 — {iv.reason_ko}")
                     + f" · 역순 {t.backward_steps} · 중복 시각 {t.duplicate_timestamps} · {t.start} ~ {t.end}")
    for n in lp.notices:
        lines.append(f"{'⚠' if n.severity == 'warning' else 'ⓘ'} [{n.code}] {n.message_ko}")
    _emit(lp.model_dump(mode="json"), args.json, "\n".join(lines), [])
    return 0


# --------------------------------------------------------------------------
def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="mlwb", description="ML Workbench CLI (P0)")
    p.add_argument("--version", action="version", version=f"mlwb {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    d = sub.add_parser("doctor", help="환경·워크스페이스·레지스트리·Provider 설정 점검")
    d.add_argument("--workspace", default=None, help="워크스페이스 폴더 (기본: $MLWB_WORKSPACE 또는 ./workspace)")
    d.add_argument("--providers", default=None)
    d.add_argument("--json", action="store_true")
    d.set_defaults(func=cmd_doctor)

    pl = sub.add_parser("plugins", help="플러그인 카탈로그").add_subparsers(dest="sub", required=True)
    ls = pl.add_parser("list")
    ls.add_argument("--kind", choices=["preprocessor", "model", "evaluator", "dataset_adapter"])
    ls.add_argument("--modality", choices=["timeseries", "vision"])
    ls.add_argument("--task")
    ls.add_argument("--ready-only", action="store_true")
    ls.add_argument("--json", action="store_true")
    ls.set_defaults(func=cmd_plugins_list)

    pr = sub.add_parser("providers", help="LLM 공급자 설정").add_subparsers(dest="sub", required=True)
    pls = pr.add_parser("list")
    pls.add_argument("--providers", default=None)
    pls.add_argument("--json", action="store_true")
    pls.set_defaults(func=cmd_providers_list)

    pn = sub.add_parser("plan", help="실행 계획").add_subparsers(dest="sub", required=True)
    pv = pn.add_parser("validate", help="계획 파일(YAML/JSON) 검증")
    pv.add_argument("file")
    pv.add_argument("--mode", choices=["draft", "final"], default="draft")
    pv.add_argument("--dataset", help="데이터셋 매니페스트(YAML/JSON). final 해시 계산에 필요")
    pv.add_argument("--assume-ready", action="store_true", help="미구현 플러그인을 실행 가능으로 가정(구조 검증용)")
    pv.add_argument("--json", action="store_true")
    pv.set_defaults(func=cmd_plan_validate)

    ex = sub.add_parser("experiment", help="실행 결과").add_subparsers(dest="sub", required=True)
    ev = ex.add_parser("verify", help="완료된 실행을 같은 seed로 다시 계산해 지표 비교(새 실행 기록 없음)")
    ev.add_argument("run_id")
    ev.add_argument("--workspace", default=None)
    ev.add_argument("--tolerance", type=float, default=1e-6)
    ev.add_argument("--json", action="store_true")
    ev.set_defaults(func=cmd_experiment_verify)

    sv = sub.add_parser("serve", help="웹 화면·API 실행 (기본 http://127.0.0.1:8000)")
    sv.add_argument("--host", default="127.0.0.1")
    sv.add_argument("--port", type=int, default=8000)
    sv.add_argument("--workspace", default=None)
    sv.set_defaults(func=cmd_serve)

    ds = sub.add_parser("datasets", help="데이터셋 등록·분석 (P1: CSV)").add_subparsers(dest="sub", required=True)
    da = ds.add_parser("add", help="CSV 등록 후 분석")
    da.add_argument("file")
    da.add_argument("--retry-of", default=None, help="실패한 데이터셋 ID에 새 파일로 다시 등록")
    da.add_argument("--no-profile", action="store_true")
    dp = ds.add_parser("profile", help="등록된 데이터셋 (재)분석")
    dp.add_argument("dataset_id")
    dl = ds.add_parser("list")
    dsh = ds.add_parser("show")
    dsh.add_argument("dataset_id")
    dsh.add_argument("--safe", action="store_true", help="LLM 전송용 요약(SafeProfile) 보기")
    for sp_, fn in ((da, cmd_datasets_add), (dp, cmd_datasets_profile), (dl, cmd_datasets_list), (dsh, cmd_datasets_show)):
        sp_.add_argument("--workspace", default=None)
        sp_.add_argument("--json", action="store_true")
        sp_.set_defaults(func=fn)
    return p


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):  # Windows 콘솔(cp949)에서 한글·기호 출력 보장
        try:
            stream.reconfigure(encoding="utf-8")  # type: ignore[attr-defined]
        except (AttributeError, ValueError):
            pass
    _setup_logging(["OPENAI_API_KEY", "LOCAL_LLM_API_KEY"])
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except WorkbenchError as e:
        print(f"오류 [{e.code}]: {e.message_ko}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
