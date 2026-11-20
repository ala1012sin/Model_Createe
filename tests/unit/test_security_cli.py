import json
import logging
from pathlib import Path

from conftest import FIX, ROOT
from mlworkbench.cli import main
from mlworkbench.core.security import RedactingFilter, find_sensitive, redact

SECRET = "sk-test-SHOULD-NEVER-APPEAR-0123456789"


def test_redact_and_find_sensitive():
    assert SECRET not in redact(f"key={SECRET}", [SECRET])
    assert find_sensitive({"profile": {"api_key": "x"}, "p": r"D:\data"}) == ["$.profile.api_key", "$.p"]
    assert find_sensitive({"columns": ["sensor_1"], "freq": "15min/avg"}) == []


def test_logging_filter_masks_env_secret(monkeypatch, caplog):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    log = logging.getLogger("mlwb.test")
    log.addFilter(RedactingFilter(["OPENAI_API_KEY"]))
    with caplog.at_level(logging.WARNING):
        log.warning("calling with %s", SECRET)
    assert SECRET not in caplog.text and "****" in caplog.text


def test_cli_doctor_never_prints_key(monkeypatch, tmp_path, capsys):
    monkeypatch.setenv("OPENAI_API_KEY", SECRET)
    rc = main(["doctor", "--workspace", str(tmp_path / "ws"), "--providers", str(ROOT / "configs/providers.yaml")])
    out = capsys.readouterr()
    assert rc == 0 and SECRET not in out.out + out.err and "OPENAI_API_KEY" in out.out
    assert (tmp_path / "ws" / "mlwb.sqlite").exists()
    assert str(tmp_path) not in out.out  # 절대경로 출력 안 함
    rc = main(["providers", "list", "--providers", str(ROOT / "configs/providers.yaml"), "--json"])
    out = capsys.readouterr().out
    assert rc == 0 and SECRET not in out and json.loads(out)["profiles"][0]["api_key_present"] is True


def test_cli_plugins_list(capsys):
    assert main(["plugins", "list", "--json"]) == 0
    items = json.loads(capsys.readouterr().out)["items"]
    # P2: 구현 어댑터가 있고 의존성이 설치된 항목만 실행 가능, 나머지는 미구현 표시
    from mlworkbench.plugins.models import IMPLEMENTED_MODELS
    from mlworkbench.plugins.timeseries import IMPLEMENTED_PREPROCESSORS
    implemented = IMPLEMENTED_MODELS | IMPLEMENTED_PREPROCESSORS
    assert items and all(i["ready"] is False for i in items if i["id"] not in implemented)
    assert {i["id"] for i in items if i["ready"]} <= implemented


def test_cli_plan_validate_exit_codes(capsys, tmp_path):
    plan = str(ROOT / "examples/timeseries_plan.yaml")
    ds = str(FIX / "ts_manifest.yaml")
    # P2: 예제 계획의 전처리·모델(session_split·resample·lag·rolling·XGBoost)은 모두 구현됨 → 통과
    assert main(["plan", "validate", plan, "--mode", "final", "--dataset", ds]) == 0
    capsys.readouterr()
    # 미구현 모델(LightGBM)로 바꾸면 차단(exit 1)
    import yaml
    body = yaml.safe_load(Path(plan).read_text(encoding="utf-8"))
    body["model"] = {"plugin_id": "timeseries.forecasting.lightgbm", "plugin_version": "1.0.0", "params": {}}
    unready = tmp_path / "unready_plan.yaml"
    unready.write_text(yaml.safe_dump(body, allow_unicode=True), encoding="utf-8")
    assert main(["plan", "validate", str(unready), "--mode", "final", "--dataset", ds]) == 1
    capsys.readouterr()
    assert main(["plan", "validate", plan, "--mode", "final", "--dataset", ds, "--assume-ready", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["plan_hash"].startswith("sha256:")
    assert main(["plan", "validate", str(ROOT / "missing.yaml")]) == 2


def test_db_uses_truncate_journal(tmp_path):
    from mlworkbench.storage.database import Workspace

    ws = Workspace(tmp_path / "w").init()
    with ws.session() as conn:
        assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "truncate"
