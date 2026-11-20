"""사용자 정의 전처리 플러그인 — 발견·명세 검증·코드 불러오기 실패·실행 래퍼(라벨 숨김·출력 규칙·과거만 사용)."""

from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

pd = pytest.importorskip("pandas")
np = pytest.importorskip("numpy")

from mlworkbench.core.registry import builtin_registry, default_registry  # noqa: E402
from mlworkbench.pipelines.ts_frame import GROUP, LABEL, SESSION, TIME, PipelineError, TsFrame  # noqa: E402
from mlworkbench.plugins.user_loader import code_hash, load_user_plugins, plugin_dirs  # noqa: E402

ROOT = Path(__file__).resolve().parents[2]
EXAMPLE = ROOT / "plugins" / "ts_custom_ewm"


def make_plugin(base: Path, folder: str = "p", *, manifest: dict | None = None, code: str | None = None,
                drop: tuple[str, ...] = ()) -> Path:
    d = base / folder
    d.mkdir(parents=True)
    m = json.loads((EXAMPLE / "plugin.json").read_text(encoding="utf-8"))
    m.update(manifest or {})
    for k in drop:
        m.pop(k, None)
    (d / "plugin.json").write_text(json.dumps(m, ensure_ascii=False), encoding="utf-8")
    (d / "plugin.py").write_text(code if code is not None else (EXAMPLE / "plugin.py").read_text(encoding="utf-8"),
                                 encoding="utf-8")
    return d


def load(base: Path):
    reg = builtin_registry()
    problems = load_user_plugins(reg, [base])
    return reg, problems


# --- 발견 ------------------------------------------------------------------------
def test_example_plugin_is_discovered_ready():
    reg, problems = load(ROOT / "plugins")
    assert problems == []
    spec = reg.get("ts.custom.ewm", "1.0.0")
    assert spec.ready and spec.origin == "user" and spec.code_sha256 == code_hash(EXAMPLE)
    assert reg.get_preprocessor_adapter("ts.custom.ewm", "1.0.0") is not None
    # 내장 카탈로그는 그대로, 사용자 플러그인이 추가되면 카탈로그 버전이 바뀐다
    assert reg.catalog_version() != builtin_registry().catalog_version()
    assert [s.id for s in reg.find(kind="preprocessor", modality="timeseries", task="forecasting")].count("ts.custom.ewm") == 1


def test_plugin_dirs_env(monkeypatch, tmp_path):
    monkeypatch.setenv("MLWB_PLUGIN_DIRS", "")
    assert plugin_dirs() == [] and default_registry().get("ts.custom.ewm", "1.0.0") is None
    make_plugin(tmp_path / "a", "x")
    make_plugin(tmp_path / "b", "y", manifest={"id": "ts.custom.ewm2"})
    monkeypatch.setenv("MLWB_PLUGIN_DIRS", f"{tmp_path / 'a'}{__import__('os').pathsep}{tmp_path / 'b'}")
    reg = default_registry()
    assert reg.get("ts.custom.ewm", "1.0.0").ready and reg.get("ts.custom.ewm2", "1.0.0").ready
    monkeypatch.delenv("MLWB_PLUGIN_DIRS")
    assert plugin_dirs() == [(ROOT / "plugins").resolve()]  # 기본: 프로젝트의 plugins 폴더


@pytest.mark.parametrize("over,drop,code", [
    ({"id": "ts.ewm"}, (), "PLUGIN_ID_NOT_ALLOWED"),                 # 내장 ID 공간 사용 불가
    ({"id": "ts.lag"}, (), "PLUGIN_ID_NOT_ALLOWED"),
    ({"kind": "model"}, (), "PLUGIN_KIND_UNSUPPORTED"),
    ({"modality": "vision"}, (), "PLUGIN_KIND_UNSUPPORTED"),
    ({"status": "ready"}, (), "PLUGIN_MANIFEST_INVALID"),            # 상태·출처·해시는 직접 쓸 수 없음
    ({"code_sha256": "x"}, (), "PLUGIN_MANIFEST_INVALID"),
    ({"entry": "plugin.py"}, (), "PLUGIN_MANIFEST_INVALID"),
    ({}, ("entry",), "PLUGIN_MANIFEST_INVALID"),
    ({}, ("fit_scope",), "PLUGIN_MANIFEST_INVALID"),
    ({"order": {"stage": 10}}, (), "PLUGIN_MANIFEST_INVALID"),       # 세션 분리·리샘플 앞 불가
    ({"order": {"stage": 40, "locked": True}}, (), "PLUGIN_MANIFEST_INVALID"),
    ({"version": "1.0"}, (), "PLUGIN_MANIFEST_INVALID"),
    ({"unknown_field": 1}, (), "PLUGIN_MANIFEST_INVALID"),
    ({"parameter_schema": {"type": "object", "properties": {"x": {"type": "nope"}}}}, (), "INVALID_PARAMETER_SCHEMA"),
])
def test_bad_manifest_is_not_registered(tmp_path, over, drop, code):
    make_plugin(tmp_path, "bad_one", manifest=over, drop=drop)
    reg, problems = load(tmp_path)
    assert [p["code"] for p in problems] == [code] and problems[0]["folder"] == "bad_one"
    assert str(tmp_path) not in json.dumps(problems, ensure_ascii=False)  # 절대경로 없음
    assert not any(s.origin == "user" and s.ready for s in reg.all())


def test_broken_json_and_non_object(tmp_path):
    d = make_plugin(tmp_path, "a")
    (d / "plugin.json").write_text("{not json", encoding="utf-8")
    d2 = make_plugin(tmp_path, "b")
    (d2 / "plugin.json").write_text("[1]", encoding="utf-8")
    _, problems = load(tmp_path)
    assert [p["code"] for p in problems] == ["PLUGIN_MANIFEST_INVALID"] * 2


@pytest.mark.parametrize("code,entry,expect", [
    ("raise ZeroDivisionError('C:/secret/path')\n", None, "PLUGIN_IMPORT_FAILED"),
    ("import sys\nsys.exit(3)\n", None, "PLUGIN_IMPORT_FAILED"),      # 서버를 멈추지 않는다
    ("class EwmFeature:\n    pass\n", None, "PLUGIN_METHOD_MISSING"),
    ("X = 1\n", None, "PLUGIN_ENTRY_INVALID"),
    ("class EwmFeature:\n    def __init__(self, a):\n        pass\n", None, "PLUGIN_IMPORT_FAILED"),
    (None, "../plugin.py:EwmFeature", "PLUGIN_ENTRY_INVALID"),         # 폴더 밖 파일 불가
    (None, "other.py:EwmFeature", "PLUGIN_ENTRY_INVALID"),
])
def test_code_problem_registers_invalid_spec(tmp_path, code, entry, expect):
    make_plugin(tmp_path, "c", code=code, manifest={"entry": entry} if entry else None)
    reg, problems = load(tmp_path)
    spec = reg.get("ts.custom.ewm", "1.0.0")
    assert spec.status == "invalid" and not spec.ready and reg.get_preprocessor_adapter(spec.id, spec.version) is None
    assert problems[0]["code"] == expect and "secret" not in spec.reason_unavailable
    assert "C:/" not in json.dumps(spec.public_view(), ensure_ascii=False)


def test_missing_dependency_does_not_import_code(tmp_path):
    marker = tmp_path / "imported.txt"
    make_plugin(tmp_path / "plugins", "d", manifest={"dependencies": ["pandas", "not_installed_mod_xyz"]},
                code=f"open({str(marker)!r}, 'w').write('x')\n")
    reg, problems = load(tmp_path / "plugins")
    spec = reg.get("ts.custom.ewm", "1.0.0")
    assert spec.status == "missing_dependency" and "not_installed_mod_xyz" in spec.reason_unavailable
    assert problems == [] and not marker.exists()


def test_duplicate_id_version(tmp_path):
    make_plugin(tmp_path, "a")
    make_plugin(tmp_path, "b")
    reg, problems = load(tmp_path)
    assert reg.get("ts.custom.ewm", "1.0.0").ready
    assert [(p["folder"], p["code"]) for p in problems] == [("b", "DUPLICATE_PLUGIN")]


def test_code_hash_tracks_edits(tmp_path):
    d = make_plugin(tmp_path, "a")
    reg, _ = load(tmp_path)
    h1 = reg.get("ts.custom.ewm", "1.0.0").code_sha256
    assert reg.current_code_hash("ts.custom.ewm", "1.0.0") == h1
    (d / "plugin.py").write_text((d / "plugin.py").read_text(encoding="utf-8") + "\n# 수정\n", encoding="utf-8")
    assert reg.current_code_hash("ts.custom.ewm", "1.0.0") != h1  # 불러온 뒤 파일이 바뀐 것을 알 수 있다
    assert load(tmp_path)[0].get("ts.custom.ewm", "1.0.0").code_sha256 != h1
    assert reg.current_code_hash("ts.lag", "1.0.0") is None


# --- 실행 래퍼 -------------------------------------------------------------------
def frame() -> TsFrame:
    """설비 A·B, 5분 간격 12행씩, A는 6번째 행부터 새 세션."""
    t = pd.date_range("2026-01-01", periods=12, freq="5min")
    parts = []
    for g, off in (("A", 0.0), ("B", 100.0)):
        x = off + np.arange(12, dtype="float64") ** 1.5
        parts.append(pd.DataFrame({GROUP: g, TIME: t, SESSION: [0] * 6 + [1] * 6 if g == "A" else [0] * 12,
                                   "temp": x, LABEL: x}))
    df = pd.concat(parts, ignore_index=True)
    return TsFrame(df, "temp", ["temp"], ["temp"])


def adapter(tmp_path, code=None, **manifest):
    make_plugin(tmp_path, "w", code=code, manifest=manifest or None)
    reg, problems = load(tmp_path)
    assert problems == [], problems
    return reg.get_preprocessor_adapter("ts.custom.ewm", "1.0.0")


def test_ewm_values_and_session_boundary(tmp_path):
    a = adapter(tmp_path)
    f = frame()
    out = a.transform(f, None, {"spans": [3]})
    assert out.feature_cols == ["temp", "temp_ewm3"] and list(out.df.columns)[-1] == "temp_ewm3"
    assert out.df[LABEL].equals(f.df[LABEL])  # 라벨은 그대로 돌아온다
    a_s1 = f.df[(f.df[GROUP] == "A") & (f.df[SESSION] == 1)]["temp"]
    want = a_s1.shift(1).ewm(span=3, min_periods=3).mean()
    got = out.df.loc[a_s1.index, "temp_ewm3"]
    assert np.allclose(got, want, equal_nan=True) and got.iloc[:3].isna().all()  # 세션 시작 뒤 3칸 이력 전엔 없음
    # 값은 현재 시점을 포함하지 않는다: 마지막 행의 현재 값을 바꿔도 피처가 같다
    g = frame()
    g.df.loc[g.df.index[-1], "temp"] = 1e9
    assert out.df["temp_ewm3"].iloc[-1] == a.transform(g, None, {"spans": [3]}).df["temp_ewm3"].iloc[-1]
    a.check_causal(f, out, {"spans": [3]})  # 과거만 사용 검사 통과
    assert a.history({"spans": [3, 12]}, "temp") == (12, {"temp": "EWM span [3, 12](현재 제외)"})


def test_label_is_hidden_from_plugin(tmp_path):
    code = (
        "from mlworkbench.plugins.sdk import UserFeature, add_feature\n"
        "SEEN = []\n"
        "class EwmFeature(UserFeature):\n"
        "    def transform(self, frame, state, params):\n"
        "        SEEN.append(list(frame.df.columns))\n"
        "        f = frame.copy(); add_feature(f, 'z', 1.0); return f\n")
    a = adapter(tmp_path, code)
    a.transform(frame(), None, {"spans": [3]})
    seen = __import__("sys").modules[a.impl.__class__.__module__].SEEN
    assert seen and all(LABEL not in cols and "temp" in cols for cols in seen)


LEAKS = {
    "next_value": "add_feature(f, 'nxt', f.df.groupby(group_keys(f))['temp'].shift(-1))",
    "global_mean": "add_feature(f, 'mean_all', f.df['temp'].mean())",
    "centered": "add_feature(f, 'c', f.df['temp'].rolling(3, center=True).mean())",
}


@pytest.mark.parametrize("name", list(LEAKS))
def test_future_use_is_detected(tmp_path, name):
    code = ("from mlworkbench.plugins.sdk import UserFeature, add_feature, group_keys\n"
            "class EwmFeature(UserFeature):\n"
            "    def transform(self, frame, state, params):\n"
            f"        f = frame.copy(); {LEAKS[name]}; return f\n")
    a = adapter(tmp_path, code)
    f = frame()
    out = a.transform(f, None, {"spans": [3]})
    with pytest.raises(PipelineError) as e:
        a.check_causal(f, out, {"spans": [3]})
    assert e.value.code == "PLUGIN_FUTURE_LEAK"


BAD_OUTPUTS = {
    "drop_row": "f.df = f.df.iloc[1:]",
    "change_existing": "f.df['temp'] = f.df['temp'] * 2",
    "change_time": f"f.df['{TIME}'] = f.df['{TIME}'] + __import__('pandas').Timedelta('1h')",
    "extra_column": "f.df['helper'] = 1.0",
    "reserved_name": "add_feature(f, '__x', 1.0)",
    "duplicate_name": "add_feature(f, 'temp', 1.0)",
    "text_feature": "add_feature(f, 'txt', 'a')",
    "listed_not_present": "f.feature_cols.append('ghost')",
    "change_target": "f.target = 'x'",
    "not_frame": "f = None",
}


@pytest.mark.parametrize("name", list(BAD_OUTPUTS))
def test_output_rules(tmp_path, name):
    code = ("from mlworkbench.plugins.sdk import UserFeature, add_feature\n"
            "class EwmFeature(UserFeature):\n"
            "    def transform(self, frame, state, params):\n"
            f"        f = frame.copy(); {BAD_OUTPUTS[name]}; return f\n")
    a = adapter(tmp_path, code)
    with pytest.raises(PipelineError) as e:
        a.transform(frame(), None, {"spans": [3]})
    assert e.value.code == "PLUGIN_OUTPUT_INVALID", e.value.message_ko


def test_runtime_error_and_bad_state(tmp_path):
    code = ("from mlworkbench.plugins.sdk import UserFeature\n"
            "class EwmFeature(UserFeature):\n"
            "    def fit(self, train, params):\n        return {'m': float('nan')}\n"
            "    def validate(self, ds, params):\n        raise ValueError('x')\n"
            "    def transform(self, frame, state, params):\n        raise KeyError('C:/data/secret.csv')\n")
    a = adapter(tmp_path, code)
    with pytest.raises(PipelineError) as e:
        a.transform(frame(), None, {})
    assert e.value.code == "PLUGIN_ERROR" and "secret" not in e.value.message_ko
    assert a.validate(None, {}) == ["플러그인 검사 중 오류가 났습니다(ValueError)"]
    shutil.rmtree(tmp_path / "w")
    b = adapter(tmp_path, code, fit_scope="train_only", order={"stage": 60})
    with pytest.raises(PipelineError) as e:
        b.fit(frame(), {})
    assert e.value.code == "PLUGIN_STATE_INVALID"
