"""계획 검증기 (CONTRACTS §5 pipeline.validate, §11 D-06/D-08/D-10).

- draft 모드: 입력 중인(비어 있는) 필드를 허용하고 현재 조합만 점검한다. 빈 필수 항목은 info로 알린다.
- final 모드: 필수값 누락·호환성·순서·누수 위험을 error로 차단한다. 통과하면 ExecutablePlan과 plan_hash를 만든다.

검증 결과(plan_hash 포함)는 제출 권한이 아니다. 제출은 승인 기록(app.plan_service)이 필요하다.
"""

from __future__ import annotations

import re
from typing import Any

from pydantic import ValidationError

from mlworkbench.core import schema_check
from mlworkbench.core.contracts import (
    BudgetNImages,
    BudgetRecentDays,
    ChronologicalSplit,
    DatasetManifest,
    DatasetState,
    DraftPlan,
    ExecutablePlan,
    ExplicitManifestSplit,
    PlanProposal,
    TimeseriesEvaluation,
    TimeseriesGoal,
    ToolSelection,
    ValidationIssue,
    ValidationResult,
    VisionAnomalyEvaluation,
    VisionGoal,
    VisionSegmentationEvaluation,
)
from mlworkbench.core.hashing import sha256_of
from mlworkbench.core.registry import PluginRegistry

_TASKS = {"timeseries": {"forecasting"}, "vision": {"anomaly_detection", "segmentation"}}
_DURATION = re.compile(r"^\d+(s|min|h|d)$")


# 판별 필드(mode/method/kind)의 태그는 경로에서 뺀다: train_budget.recent_days.days → train_budget.days
_UNION_TAGS = {"all", "recent_days", "n_images", "chronological", "explicit_manifest", "timeseries",
               "vision_anomaly", "vision_segmentation"}


def _message_ko(e: dict) -> str:
    t, ctx = e.get("type", ""), e.get("ctx") or {}
    if t == "value_error":
        return str(ctx.get("error", e.get("msg", ""))).removeprefix("Value error, ")
    table = {
        "greater_than": "{gt}보다 커야 합니다", "greater_than_equal": "{ge} 이상이어야 합니다",
        "less_than": "{lt}보다 작아야 합니다", "less_than_equal": "{le} 이하여야 합니다",
        "too_short": "하나 이상 선택하세요", "missing": "필수 항목입니다",
        "int_parsing": "정수를 입력하세요", "int_type": "정수를 입력하세요", "int_from_float": "정수를 입력하세요",
        "float_parsing": "숫자를 입력하세요", "float_type": "숫자를 입력하세요",
        "literal_error": "허용되지 않는 값입니다", "union_tag_invalid": "허용되지 않는 선택입니다",
        "extra_forbidden": "정의되지 않은 항목입니다", "string_type": "문자열이어야 합니다",
    }
    if t in table:
        nice = {k: (int(v) if isinstance(v, float) and v.is_integer() else v) for k, v in ctx.items()}
        try:
            return table[t].format(**nice)
        except (KeyError, IndexError):
            return table[t]
    return f"형식 오류: {e.get('msg', '')}"


def pydantic_issues(err: ValidationError, prefix: str = "") -> list[ValidationIssue]:
    out = []
    for e in err.errors():
        loc = prefix
        for i, part in enumerate(e["loc"]):
            if i == 1 and isinstance(part, str) and part in _UNION_TAGS:
                continue
            loc += f"[{part}]" if isinstance(part, int) else (f".{part}" if loc else str(part))
        out.append(ValidationIssue(code="SCHEMA_INVALID", severity="error", path=loc or "$", message_ko=_message_ko(e)))
    return out


# 이전 초안에 남을 수 있는, 더 이상 제공하지 않는 파라미터 값 → (메시지, 고칠 방법)
_LEGACY_VALUES = {
    ("ts.missing", "method", "linear"): (
        "선형 보간은 다음 관측값(미래)을 써서 예측에는 사용할 수 없습니다",
        "전처리 화면의 결측 처리에서 '직전 값으로 채움'이나 '행 제외'를 고르세요"),
}
_TS_BASELINES_IMPLEMENTED = {"naive_last_value", "none"}
_DUR_UNITS = {"s": 1, "min": 60, "h": 3600, "d": 86400}


def _seconds(text: str) -> int | None:
    import re as _re

    m = _re.match(r"^(\d+)(s|min|h|d)$", text or "")
    return int(m.group(1)) * _DUR_UNITS[m.group(2)] if m else None


class _Collector:
    def __init__(self) -> None:
        self.issues: list[ValidationIssue] = []

    def add(self, severity: str, code: str, path: str, msg: str, hint: str | None = None) -> None:
        self.issues.append(ValidationIssue(code=code, severity=severity, path=path, message_ko=msg, fix_hint_ko=hint))

    def err(self, *a: Any, **k: Any) -> None:
        self.add("error", *a, **k)

    def warn(self, *a: Any, **k: Any) -> None:
        self.add("warning", *a, **k)

    def info(self, *a: Any, **k: Any) -> None:
        self.add("info", *a, **k)


class PlanValidator:
    def __init__(self, registry: PluginRegistry):
        self.registry = registry

    # 공개 API ---------------------------------------------------------------
    def parse_draft(self, data: dict[str, Any] | DraftPlan | PlanProposal) -> tuple[DraftPlan | None, list[ValidationIssue]]:
        if isinstance(data, DraftPlan):
            return data, []
        if isinstance(data, PlanProposal):
            return data.to_draft(), []
        try:
            return DraftPlan.model_validate(data), []
        except ValidationError as e:
            return None, pydantic_issues(e)

    def validate(
        self,
        plan: dict[str, Any] | DraftPlan | PlanProposal,
        *,
        mode: str = "draft",
        dataset: DatasetManifest | None = None,
        required_user_inputs: list[str] | None = None,
    ) -> tuple[ValidationResult, ExecutablePlan | None]:
        if isinstance(plan, PlanProposal):
            required_user_inputs = (required_user_inputs or []) + plan.required_user_inputs
        draft, issues = self.parse_draft(plan)
        if draft is None:
            return ValidationResult(mode=mode, issues=issues), None  # type: ignore[arg-type]

        c = _Collector()
        final = mode == "final"
        self._check_goal(draft, c, final)
        self._check_dataset(draft, dataset, c, final)
        self._check_tools(draft, dataset, c, final)
        self._check_order(draft, c)
        self._check_budget_split_eval(draft, dataset, c, final)
        for i, item in enumerate(required_user_inputs or []):
            (c.err if final else c.info)(
                "REQUIRED_USER_INPUT", f"required_user_inputs[{i}]", f"사용자 확인이 필요합니다: {item}"
            )

        result = ValidationResult(mode=mode, issues=c.issues)  # type: ignore[arg-type]
        if not (final and result.ok):
            return result, None
        if dataset is None or dataset.version is None:
            return result, None
        exe = ExecutablePlan(
            dataset_id=draft.dataset_id,
            dataset_version=dataset.version,  # 서버가 확정한 데이터 버전
            modality=draft.modality,
            task=draft.task,
            user_goal=draft.user_goal,  # type: ignore[arg-type]
            preprocessing=draft.preprocessing,
            model=draft.model,  # type: ignore[arg-type]
            train_budget=draft.train_budget,
            split_policy=draft.split_policy,  # type: ignore[arg-type]
            evaluation=draft.evaluation,  # type: ignore[arg-type]
        )
        result.plan_hash = sha256_of(exe.execution_config())
        return result, exe

    # 세부 규칙 -------------------------------------------------------------
    def _check_goal(self, d: DraftPlan, c: _Collector, final: bool) -> None:
        if d.task not in _TASKS[d.modality]:
            c.err("TASK_UNSUPPORTED", "task", f"{d.modality}에서 지원하지 않는 태스크입니다: {d.task}")
        g = d.user_goal
        if g is None:
            (c.err if final else c.info)("GOAL_REQUIRED", "user_goal", "예측 목표를 아직 확정하지 않았습니다",
                                          "데이터셋 화면에서 목표를 확정하세요")
            return
        if g.modality != d.modality:
            c.err("GOAL_MODALITY_MISMATCH", "user_goal.modality", "목표의 데이터 종류가 계획과 다릅니다")
            return
        if g.task != d.task:
            c.err("GOAL_TASK_MISMATCH", "user_goal.task", f"목표 태스크({g.task})와 계획 태스크({d.task})가 다릅니다")
        missing = (c.err if final else c.info)
        if isinstance(g, TimeseriesGoal):
            for f in ("timestamp_col", "target_col"):
                if not getattr(g, f):
                    missing("GOAL_FIELD_REQUIRED", f"user_goal.{f}", f"{'시간열' if f == 'timestamp_col' else '예측 타깃'}을 선택하세요")
            if not g.horizons:
                missing("GOAL_FIELD_REQUIRED", "user_goal.horizons", "예측 구간을 하나 이상 추가하세요")
            for i, h in enumerate(g.horizons):
                if not _DURATION.match(h):
                    c.err("GOAL_FIELD_INVALID", f"user_goal.horizons[{i}]", f"예측 구간 형식이 올바르지 않습니다: {h}", "예: 15min, 1h")
            if g.target_col and g.target_col in (g.timestamp_col, g.group_col):
                c.err("GOAL_FIELD_INVALID", "user_goal.target_col", "타깃은 시간열·설비 구분 열과 달라야 합니다")
            if g.group_col and g.group_col == g.timestamp_col:
                c.err("GOAL_FIELD_INVALID", "user_goal.group_col", "설비 구분 열은 시간열과 달라야 합니다")
            if len(set(g.horizons)) != len(g.horizons):
                c.err("GOAL_FIELD_INVALID", "user_goal.horizons", "같은 예측 구간이 중복되었습니다")
        elif isinstance(g, VisionGoal):
            if g.train_policy is None:
                missing("GOAL_FIELD_REQUIRED", "user_goal.train_policy", "학습 방식(정상 기반/지도학습)을 확정하세요")
            elif g.task == "anomaly_detection" and g.train_policy != "normal_only":
                c.err("GOAL_FIELD_INVALID", "user_goal.train_policy", "이상탐지는 정상 이미지 기반(normal_only) 학습입니다")
            elif g.task == "segmentation" and g.train_policy != "supervised":
                c.err("GOAL_FIELD_INVALID", "user_goal.train_policy", "세그멘테이션은 라벨 기반(supervised) 학습입니다")

    def _check_dataset(self, d: DraftPlan, ds: DatasetManifest | None, c: _Collector, final: bool) -> None:
        if ds is None:
            c.warn("DATASET_CONTEXT_MISSING", "dataset_id", "데이터셋 정보 없이 계획 구조만 검증했습니다",
                   "승인 전에는 등록된 데이터셋으로 다시 검증합니다")
            return
        if ds.dataset_id != d.dataset_id:
            c.err("DATASET_MISMATCH", "dataset_id", "계획의 데이터셋과 선택한 데이터셋이 다릅니다")
        if ds.modality != d.modality:
            c.err("DATASET_MODALITY_MISMATCH", "modality", f"데이터셋은 {ds.modality} 데이터입니다")
        if final and (ds.state != DatasetState.PROFILED or not ds.version):
            c.err("DATASET_NOT_READY", "dataset_id", f"데이터셋 분석이 끝나지 않았습니다 (상태: {ds.state.value})",
                  "데이터셋 화면에서 분석 완료를 확인하세요")
        g = d.user_goal
        if isinstance(g, TimeseriesGoal) and ds.columns:
            cols = {col.name: col for col in ds.columns}
            for f in ("timestamp_col", "target_col", "group_col"):
                v = getattr(g, f)
                if v and v not in cols:
                    c.err("GOAL_COLUMN_MISSING", f"user_goal.{f}", f"데이터셋에 없는 열입니다: {v}")
            if g.timestamp_col in cols and cols[g.timestamp_col].dtype != "datetime":
                c.warn("GOAL_COLUMN_TYPE", "user_goal.timestamp_col", "선택한 시간열의 타입이 시간으로 인식되지 않았습니다")
            if g.target_col in cols and cols[g.target_col].dtype != "numeric":
                c.err("GOAL_COLUMN_TYPE", "user_goal.target_col", "예측 타깃은 수치형 열이어야 합니다")

    def _tool(self, sel: ToolSelection, path: str, kind: str, d: DraftPlan, ds: DatasetManifest | None,
              c: _Collector, final: bool) -> None:
        if not self.registry.has(sel.plugin_id):
            c.err("UNKNOWN_PLUGIN", f"{path}.plugin_id", f"등록되지 않은 도구입니다: {sel.plugin_id} — 실행할 수 없습니다")
            return
        spec = self.registry.get(sel.plugin_id, sel.plugin_version)
        if spec is None:
            c.err("UNKNOWN_VERSION", f"{path}.plugin_version",
                  f"{sel.plugin_id}에 {sel.plugin_version} 버전이 없습니다",
                  f"사용 가능한 버전: {', '.join(self.registry.versions(sel.plugin_id))}")
            return
        if spec.kind != kind:
            c.err("WRONG_PLUGIN_KIND", f"{path}.plugin_id", f"{spec.display_name_ko}은(는) {kind} 자리에 쓸 수 없습니다")
        if spec.modality != d.modality:
            c.err("INCOMPATIBLE_MODALITY", f"{path}.plugin_id", f"{spec.display_name_ko}은(는) {spec.modality} 전용입니다")
        elif d.task not in spec.tasks:
            c.err("INCOMPATIBLE_TASK", f"{path}.plugin_id", f"{spec.display_name_ko}은(는) {d.task} 태스크를 지원하지 않습니다")
        if not spec.ready:
            c.err("PLUGIN_NOT_READY", f"{path}.plugin_id",
                  f"{spec.display_name_ko}({spec.key})는 실행할 수 없습니다: {spec.reason_unavailable or spec.status}")
        if ds is not None:
            for art in spec.required_artifacts:
                if art not in ds.artifacts:
                    c.err("MISSING_REQUIRED_ARTIFACT", f"{path}.plugin_id",
                          f"{spec.display_name_ko}에는 {art}가 필요하지만 데이터셋에 없습니다")
        parameter_issues = schema_check.check(sel.params, spec.parameter_schema, require_all=final)
        for p, code, msg in parameter_issues:
            sev = "error"
            hint = None
            legacy = _LEGACY_VALUES.get((sel.plugin_id, p, str(sel.params.get(p))))
            if legacy:  # 이전 버전 초안에 남은, 더 이상 제공하지 않는 값
                code, msg, hint = "LEGACY_PARAM_VALUE", legacy[0], legacy[1]
            c.add(sev, "INVALID_PARAM" if code not in ("PARAM_REQUIRED", "LEGACY_PARAM_VALUE") else code,
                  f"{path}.params.{p}" if p else f"{path}.params", f"{spec.display_name_ko}: {msg}", hint)
        if final and not parameter_issues and kind == spec.kind == "preprocessor" and spec.modality == d.modality and d.task in spec.tasks:
            adapter = self.registry.get_preprocessor_adapter(sel.plugin_id, sel.plugin_version)
            if adapter is not None:
                for message in adapter.validate(ds, sel.params):
                    c.err("INVALID_PARAM", f"{path}.params", f"{spec.display_name_ko}: {message}")

    def _check_tools(self, d: DraftPlan, ds: DatasetManifest | None, c: _Collector, final: bool) -> None:
        seen: dict[str, int] = {}
        for i, sel in enumerate(d.preprocessing):
            path = f"preprocessing[{i}]"
            if sel.plugin_id in seen:
                c.err("DUPLICATE_STEP", f"{path}.plugin_id", f"같은 전처리가 {seen[sel.plugin_id] + 1}번과 중복됩니다")
            seen.setdefault(sel.plugin_id, i)
            self._tool(sel, path, "preprocessor", d, ds, c, final)
        if d.model is None:
            (c.err if final else c.info)("MODEL_REQUIRED", "model", "모델을 아직 선택하지 않았습니다", "학습 설정에서 모델을 고르세요")
        else:
            self._tool(d.model, "model", "model", d, ds, c, final)

    def _check_order(self, d: DraftPlan, c: _Collector) -> None:
        positions = {sel.plugin_id: i for i, sel in enumerate(d.preprocessing)}
        prev_stage, prev_name = -1, ""
        for i, sel in enumerate(d.preprocessing):
            spec = self.registry.latest(sel.plugin_id)
            if spec is None:
                continue
            for dep in spec.order.must_follow:
                if dep in positions and positions[dep] > i:
                    c.err("ORDER_VIOLATION", f"preprocessing[{i}]",
                          f"{spec.display_name_ko}은(는) {dep} 뒤에 와야 합니다", "순서를 되돌리세요")
            if spec.order.stage < prev_stage:
                c.err("ORDER_VIOLATION", f"preprocessing[{i}]",
                      f"{spec.display_name_ko}은(는) {prev_name}보다 앞 단계에서 실행되어야 합니다",
                      "🔒 고정 단계의 순서는 바꿀 수 없습니다" if spec.order.locked else None)
            prev_stage, prev_name = max(prev_stage, spec.order.stage), spec.display_name_ko

    @staticmethod
    def _check_horizon_grid(d: DraftPlan, c: _Collector) -> None:
        """리샘플이 있으면 예측 시점은 간격의 정수배여야 정확한 미래 시각의 타깃을 맞출 수 있다."""
        freq = next((sel.params.get("frequency") for sel in d.preprocessing if sel.plugin_id == "ts.resample"), None)
        f = _seconds(freq) if isinstance(freq, str) else None
        if not f:
            return
        for i, h in enumerate(d.user_goal.horizons):  # type: ignore[union-attr]
            hs = _seconds(h)
            if hs and (hs % f != 0):
                c.err("HORIZON_NOT_MULTIPLE", f"user_goal.horizons[{i}]",
                      f"예측 시점 {h}가 리샘플 간격 {freq}의 정수배가 아닙니다",
                      "예측 시점이나 리샘플 간격을 맞춰 주세요")

    def _check_budget_split_eval(self, d: DraftPlan, ds: DatasetManifest | None, c: _Collector, final: bool) -> None:
        b, s, e = d.train_budget, d.split_policy, d.evaluation
        if isinstance(b, BudgetRecentDays) and d.modality != "timeseries":
            c.err("BUDGET_INCOMPATIBLE", "train_budget", "최근 N일은 시계열에서만 쓸 수 있습니다")
        if isinstance(b, BudgetNImages) and d.modality != "vision":
            c.err("BUDGET_INCOMPATIBLE", "train_budget", "N장 선택은 이미지 데이터에서만 쓸 수 있습니다")
        if isinstance(b, BudgetNImages) and ds is not None and ds.image_count is not None and b.count > ds.image_count:
            c.err("BUDGET_TOO_LARGE", "train_budget.count", f"전체 이미지({ds.image_count}장)보다 많습니다")

        if s is None:
            (c.err if final else c.info)("SPLIT_REQUIRED", "split_policy", "분할 방식을 정하세요")
        elif d.modality == "timeseries" and not isinstance(s, ChronologicalSplit):
            c.err("SPLIT_INCOMPATIBLE", "split_policy.method", "시계열은 시간순 분할만 허용합니다(미래 정보 누수 방지)")
        elif d.modality == "vision" and not isinstance(s, ExplicitManifestSplit):
            c.err("SPLIT_INCOMPATIBLE", "split_policy.method", "이미지 데이터는 역할이 지정된 분할(explicit_manifest)을 사용합니다")
        elif isinstance(s, ChronologicalSplit) and s.val == 0:
            c.warn("SPLIT_NO_VALIDATION", "split_policy.val", "검증 구간이 없습니다")
        elif isinstance(s, ExplicitManifestSplit):
            roles = [("train", s.train), ("calibration", s.calibration), ("validation", s.validation), ("test", s.test)]
            used = [(k, v) for k, v in roles if v]
            names = [v for _, v in used]
            if len(set(names)) != len(names):
                c.err("LEAKAGE_SPLIT_OVERLAP", "split_policy", "학습·보정·검증·테스트에 같은 데이터 역할을 중복 사용할 수 없습니다")
            if s.calibration and s.calibration_from_train:
                c.err("SPLIT_INVALID", "split_policy.calibration_from_train", "보정 데이터를 지정했으면 학습 데이터에서 떼어낼 수 없습니다")
            if ds is not None and ds.split_roles:
                for k, v in used:
                    if v not in ds.split_roles:
                        c.err("SPLIT_ROLE_MISSING", f"split_policy.{k}", f"데이터셋에 '{v}' 역할이 없습니다",
                              "데이터셋 화면에서 폴더 역할을 확정하세요")

        if e is None:
            (c.err if final else c.info)("EVALUATION_REQUIRED", "evaluation", "평가 방식을 정하세요")
            return
        expected = {"forecasting": TimeseriesEvaluation, "anomaly_detection": VisionAnomalyEvaluation,
                    "segmentation": VisionSegmentationEvaluation}.get(d.task)
        if expected and not isinstance(e, expected):
            c.err("EVALUATION_INCOMPATIBLE", "evaluation.kind", f"{d.task}에 맞지 않는 평가 방식입니다")
            return
        if isinstance(e, TimeseriesEvaluation) and e.baseline not in _TS_BASELINES_IMPLEMENTED:
            (c.err if final else c.warn)("EVAL_OPTION_NOT_IMPLEMENTED", "evaluation.baseline",
                                         "선택한 기준 예측은 아직 구현되지 않았습니다", "직전값 또는 사용 안 함을 고르세요")
        if d.modality == "timeseries" and isinstance(d.user_goal, TimeseriesGoal):
            self._check_horizon_grid(d, c)
        if isinstance(e, TimeseriesEvaluation) and e.per_group and not (
                isinstance(d.user_goal, TimeseriesGoal) and d.user_goal.group_col):
            c.err("EVALUATION_GROUP_MISSING", "evaluation.per_group",
                  "설비 구분 열을 정하지 않아 설비별로 집계할 수 없습니다", "데이터셋 화면의 예측 목표에서 설비 구분 열을 고르세요")
        if isinstance(e, VisionAnomalyEvaluation):
            if e.threshold.method.startswith("calibration") and isinstance(s, ExplicitManifestSplit) \
                    and not (s.calibration or s.calibration_from_train):
                c.err("CALIBRATION_REQUIRED", "evaluation.threshold",
                      "보정 데이터 기반 임계값에는 보정용 정상 이미지가 필요합니다",
                      "보정 데이터를 지정하거나 정상 학습 이미지에서 떼어내세요")
            if e.pixel_metrics and ds is not None and "ground_truth_mask" not in ds.artifacts:
                c.warn("PIXEL_METRIC_NA", "evaluation.pixel_metrics",
                       "GT 마스크가 없어 AU-PRO는 계산하지 않고 N/A로 기록합니다")
