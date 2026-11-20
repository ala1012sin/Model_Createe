import { useCallback, useEffect, useId, useRef, useState } from "react";
import { BackLink } from "../toast";
import { ApiError, api } from "../api";
import type { EditGuard } from "../App";
import type { ModelOption, TrainingFields, TrainingView, TsEvaluation, ValidationIssue } from "../types";
import { intervalKo } from "../format";
import { ParamEditor } from "./ParamEditor";

// 분할 비율은 화면에서 %로 다루고 저장할 때 0~1로 바꾼다
type Pct = { train: string; val: string; test: string };
const toPct = (x: number) => String(Math.round(x * 10000) / 100);
const toFrac = (p: string) => (p.trim() === "" || Number.isNaN(Number(p)) ? NaN : Math.round(Number(p) * 100) / 10000);

const METRICS: { key: TsEvaluation["metrics"][number]; label: string; hint: string }[] = [
  { key: "mae", label: "MAE", hint: "평균 절대 오차" },
  { key: "rmse", label: "RMSE", hint: "큰 오차에 더 민감" },
  { key: "r2", label: "R²", hint: "설명된 분산 비율" },
  { key: "smape", label: "sMAPE", hint: "대칭 백분율 오차" },
];

const HORIZON_SECONDS: Record<string, number> = { s: 1, min: 60, h: 3600, d: 86400 };
function maxHorizon(hs: string[]): string | null {
  let best: string | null = null;
  let bestS = -1;
  for (const h of hs) {
    const m = h.match(/^(\d+)(s|min|h|d)$/);
    if (m && Number(m[1]) * HORIZON_SECONDS[m[2]] > bestS) {
      bestS = Number(m[1]) * HORIZON_SECONDS[m[2]];
      best = h;
    }
  }
  return best;
}

function Msgs({ issues }: { issues: ValidationIssue[] }) {
  return (
    <>
      {issues.map((i, n) => (
        <p key={n} className={i.severity === "error" ? "field-error" : "field-warn"}>
          {i.message_ko}
          {i.fix_hint_ko ? ` — ${i.fix_hint_ko}` : ""}
        </p>
      ))}
    </>
  );
}

export function TrainingPage({
  datasetId,
  datasetsHref,
  preprocessHref,
  confirmHref,
  onGuard,
}: {
  datasetId: string;
  datasetsHref: string;
  preprocessHref: string;
  confirmHref: string;
  onGuard: (g: EditGuard | null) => void;
}) {
  const ids = { days: useId(), train: useId(), val: useId(), test: useId(), baseline: useId() };
  const [view, setView] = useState<TrainingView | null>(null);
  const [loadError, setLoadError] = useState<{ code: string; message: string } | null>(null);
  const [model, setModel] = useState<TrainingFields["model"]>(null);
  const [budgetMode, setBudgetMode] = useState<"all" | "recent_days">("all");
  const [days, setDays] = useState("30");
  const [pct, setPct] = useState<Pct>({ train: "70", val: "15", test: "15" });
  const [evaluation, setEvaluation] = useState<TsEvaluation | null>(null);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [dirty, setDirty] = useState(false);
  const [conflict, setConflict] = useState<string | null>(null);
  const [save, setSave] = useState<{ kind: "idle" | "saving" | "saved" | "error"; text?: string }>({ kind: "idle" });

  const adopt = useCallback((v: TrainingView) => {
    setView(v);
    setModel(v.model);
    setBudgetMode(v.train_budget.mode);
    if (v.train_budget.mode === "recent_days" && v.train_budget.days != null) setDays(String(v.train_budget.days));
    setPct({ train: toPct(v.split_policy.train), val: toPct(v.split_policy.val), test: toPct(v.split_policy.test) });
    setEvaluation(v.evaluation);
    setIssues(v.issues);
    setDirty(false);
    setConflict(null);
  }, []);

  const load = useCallback(async () => {
    try {
      adopt(await api.training(datasetId));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof ApiError ? { code: e.body.code, message: e.message } : { code: "?", message: "불러오지 못했습니다" });
    }
  }, [datasetId, adopt]);

  useEffect(() => {
    setView(null);
    void load();
  }, [load]);

  const fields = (): TrainingFields | null => {
    if (!evaluation) return null;
    return {
      model,
      train_budget: budgetMode === "all" ? { mode: "all" } : { mode: "recent_days", days: days.trim() === "" ? null : Number(days) },
      split_policy: { method: "chronological", train: toFrac(pct.train), val: toFrac(pct.val), test: toFrac(pct.test), gap: "auto" },
      evaluation,
    };
  };

  const edit = (fn: () => void) => {
    fn();
    setDirty(true);
    setSave({ kind: "idle" });
  };

  // 편집 0.5초 뒤 서버 검증(저장하지 않음)
  useEffect(() => {
    if (!dirty) return;
    const f = fields();
    if (!f) return;
    const t = window.setTimeout(() => {
      api
        .validateTraining(datasetId, f)
        .then((r) => setIssues(r.issues))
        .catch(() => undefined);
    }, 500);
    return () => window.clearTimeout(t);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [dirty, model, budgetMode, days, pct, evaluation, datasetId]);

  const doSave = async (): Promise<boolean> => {
    const f = fields();
    if (!view || !f) return false;
    setSave({ kind: "saving" });
    try {
      const v = await api.saveTraining(datasetId, { ...f, expected_revision: view.revision });
      adopt(v);
      setSave({ kind: "saved", text: "학습 설정을 저장했습니다" });
      return true;
    } catch (e) {
      if (e instanceof ApiError && e.body.code === "REVISION_CONFLICT") {
        setConflict(e.message);
        setSave({ kind: "idle" });
        return false;
      }
      if (e instanceof ApiError && e.body.issues) setIssues(e.body.issues);
      setSave({ kind: "error", text: e instanceof ApiError ? e.message : "저장하지 못했습니다" });
      return false;
    }
  };

  const saveRef = useRef(doSave);
  saveRef.current = doSave;
  useEffect(() => {
    onGuard({ dirty, save: () => saveRef.current() });
  }, [dirty, onGuard]);
  useEffect(() => () => onGuard(null), [onGuard]);

  // 오류가 있어도 확인 화면으로 갈 수 있다(실행 차단은 확인 화면에서 일괄). 저장하지 않은 변경은 먼저 저장한다.
  const goConfirm = async () => {
    if (dirty && !(await doSave())) return;
    window.location.hash = confirmHref;
  };

  // --- 화면 ---------------------------------------------------------------
  if (loadError) {
    return (
      <div className="train">
        <h1 className="page-title">학습 설정</h1>
        <div className="panel-empty" role="alert">
          <h2 className="panel-title">
            {loadError.code === "GOAL_REQUIRED" ? "예측 목표를 먼저 저장해 주세요" : "학습 설정을 불러오지 못했습니다"}
          </h2>
          <p>{loadError.code === "GOAL_REQUIRED" ? "데이터셋 화면에서 예측 목표를 저장하면 학습 설정을 정할 수 있습니다." : loadError.message}</p>
          <div className="actions">
            <a className="btn btn-primary" href={datasetsHref}>
              데이터셋 화면으로
            </a>
          </div>
        </div>
      </div>
    );
  }
  if (!view || !evaluation) {
    return (
      <div className="train" aria-busy="true">
        <h1 className="page-title">학습 설정</h1>
        <p className="muted">초안을 불러오는 중입니다.</p>
      </div>
    );
  }

  const g = view.goal;
  const at = (prefix: string) => issues.filter((i) => i.path === prefix || i.path.startsWith(prefix + ".") || i.path.startsWith(prefix + "["));
  const exact = (path: string) => issues.filter((i) => i.path === path);
  const selected: ModelOption | undefined = view.models.find((m) => m.plugin_id === model?.plugin_id);
  const nums = [pct.train, pct.val, pct.test].map(Number);
  const sum = nums.reduce((a, b) => a + (Number.isNaN(b) ? 0 : b), 0);
  const sumOk = Math.abs(sum - 100) < 0.01;
  const gapH = maxHorizon(g.horizons);
  const errs = issues.filter((i) => i.severity === "error" && i.code !== "PLUGIN_NOT_READY");
  const notReady = issues.some((i) => i.code === "PLUGIN_NOT_READY");
  const w = (p: string) => `${Math.max(0, Math.min(100, Number(p) || 0))}%`;

  return (
    <div className="train">
      <BackLink href={preprocessHref} label="전처리" />
      <h1 className="page-title">학습 설정</h1>
      <p className="goal-line">
        <strong>{view.dataset_name}</strong>의 <strong>{g.target_col}</strong>을(를){" "}
        {g.horizons.map((h) => `${intervalKo(h)} 뒤`).join(", ")} 예측
        {g.group_col ? <> (설비 구분 {g.group_col})</> : null}. <a href={datasetsHref}>목표 바꾸기</a>
      </p>
      <p className="prep-line">
        전처리 초안: {view.preprocessing.length ? view.preprocessing.join(" → ") : "없음"}
        {view.excluded.length ? ` (제외: ${view.excluded.join(", ")})` : ""} <a href={preprocessHref}>전처리 바꾸기</a>
      </p>

      {(notReady || (selected && selected.status !== "ready")) && (
        <p className="preview-note" role="note">
          계획 미리보기: 선택한 모델·전처리가 아직 구현 전이라 설정을 저장하는 것까지만 할 수 있습니다. 확인·실행 화면에서 승인이 차단됩니다.
        </p>
      )}
      {view.defaults_applied.length > 0 && !dirty && (
        <p className="muted defaults-note">
          저장된 분할·평가 설정이 없어 기본값(시간순 70/15/15, MAE·RMSE, 직전값 기준 예측)을 채웠습니다. 저장해야 초안에 반영됩니다.
        </p>
      )}
      {conflict && (
        <div className="banner banner-warn" role="alert">
          <p>{conflict}</p>
          <button type="button" className="btn btn-small" onClick={() => void load()}>
            최신 상태 불러오기
          </button>
          <span className="muted">지금 화면의 저장하지 않은 변경은 사라집니다.</span>
        </div>
      )}

      {/* A. 모델 ------------------------------------------------------------ */}
      <section className="tsec" aria-labelledby="sec-model">
        <h2 className="tsec-title" id="sec-model">
          모델
        </h2>
        <fieldset className="model-list">
          <legend className="visually-hidden">모델 선택</legend>
          {view.models.map((m) => (
            <label key={m.plugin_id} className={`model-opt${model?.plugin_id === m.plugin_id ? " is-selected" : ""}`}>
              <input
                type="radio"
                name="model"
                checked={model?.plugin_id === m.plugin_id}
                onChange={() =>
                  edit(() =>
                    setModel({
                      plugin_id: m.plugin_id,
                      plugin_version: m.plugin_version,
                      params: view.model?.plugin_id === m.plugin_id ? view.model.params : {},
                      reason: view.model?.plugin_id === m.plugin_id ? view.model.reason : "",
                    }),
                  )
                }
              />
              <span className="model-main">
                <span className="model-name">
                  {m.display_name_ko} <span className="param-key">{m.plugin_id}@{m.plugin_version}</span>
                </span>
                <span className="card-meta">
                  {m.recommended && <span className="tag">추천</span>}
                  <span className={`status status-${m.status === "ready" ? "done" : "busy"}`}>{m.status_label}</span>
                  {m.reason_unavailable && <span className="muted">{m.reason_unavailable}</span>}
                  {m.requires_gpu && <span className="muted">GPU 필요</span>}
                </span>
                <span className="muted">{m.description_ko}</span>
              </span>
            </label>
          ))}
        </fieldset>
        <Msgs issues={at("model").filter((i) => !i.path.startsWith("model.params") && i.code !== "PLUGIN_NOT_READY")} />
        {selected && model && (
          <div className="model-params">
            <h3 className="sub-title">파라미터</h3>
            <ParamEditor
              key={model.plugin_id}
              schema={selected.parameter_schema}
              params={model.params}
              columns={[]}
              issues={at("model.params")}
              pathPrefix="model.params"
              onChange={(params) => edit(() => setModel({ ...model, params }))}
            />
          </div>
        )}
      </section>

      {/* B. 학습 데이터량 ------------------------------------------------------ */}
      <section className="tsec" aria-labelledby="sec-budget">
        <h2 className="tsec-title" id="sec-budget">
          학습 데이터량
        </h2>
        <div className="radio-row">
          <label className="check">
            <input type="radio" name="budget" checked={budgetMode === "all"} onChange={() => edit(() => setBudgetMode("all"))} />
            학습 구간 전체
          </label>
          <label className="check">
            <input type="radio" name="budget" checked={budgetMode === "recent_days"} onChange={() => edit(() => setBudgetMode("recent_days"))} />
            학습 구간 중 최근
          </label>
          <input
            id={ids.days}
            className="num-input"
            type="number"
            min={1}
            step={1}
            aria-label="최근 일수"
            value={days}
            disabled={budgetMode !== "recent_days"}
            onChange={(e) => edit(() => setDays(e.target.value))}
          />
          <span>일</span>
        </div>
        <Msgs issues={at("train_budget")} />
        <div className="split-figure" role="img"
          aria-label={`개념도: 학습 ${pct.train}%, 검증 ${pct.val}%, 테스트 ${pct.test}% 시간순 분할${budgetMode === "recent_days" ? `, 학습 구간 중 최근 ${days}일만 사용` : ""}`}>
          <div className="seg seg-train" style={{ width: w(pct.train) }}>
            {budgetMode === "recent_days" && <span className="seg-off">제외</span>}
            <span className={`seg-used${budgetMode === "recent_days" ? " is-partial" : ""}`}>
              {budgetMode === "recent_days" ? `최근 ${days || "?"}일 사용` : "학습 전체 사용"}
            </span>
          </div>
          <div className="seg seg-val" style={{ width: w(pct.val) }}>
            {Number(pct.val) > 0 ? "검증" : ""}
          </div>
          <div className="seg seg-test" style={{ width: w(pct.test) }}>
            테스트
          </div>
        </div>
        <div className="split-legend" aria-hidden="true">
          <span>과거</span>
          <span>
            학습 {pct.train}% · 검증 {pct.val}% · 테스트 {pct.test}%
          </span>
          <span>최근</span>
        </div>
        <p className="muted">
          학습량을 바꿔도 검증·테스트 구간은 그대로입니다. 그림은 비율을 보여 주는 개념도이며, 실제 사용 기간과 행 수는 학습을 실행할 때 계산해 기록합니다.
        </p>
      </section>

      {/* C. 분할·평가 ---------------------------------------------------------- */}
      <section className="tsec" aria-labelledby="sec-eval">
        <h2 className="tsec-title" id="sec-eval">
          분할·평가
        </h2>
        <div className="split-row">
          <span className="split-method">시간순 분할</span>
          {(["train", "val", "test"] as const).map((k) => (
            <div key={k} className="field split-field">
              <label htmlFor={ids[k]}>{k === "train" ? "학습" : k === "val" ? "검증" : "테스트"} (%)</label>
              <input
                id={ids[k]}
                type="number"
                min={0}
                max={100}
                step={1}
                value={pct[k]}
                onChange={(e) => edit(() => setPct({ ...pct, [k]: e.target.value }))}
              />
              <Msgs issues={exact(`split_policy.${k}`)} />
            </div>
          ))}
          <p className={`split-sum${sumOk ? "" : " is-bad"}`} aria-live="polite">
            합계 {Math.round(sum * 100) / 100}%
          </p>
        </div>
        <Msgs issues={exact("split_policy").concat(issues.filter((i) => i.path.startsWith("split_policy.") && !/^split_policy\.(train|val|test)$/.test(i.path)))} />
        <p className="muted">
          구간 사이 간격: 자동 — 가장 긴 예측 구간({gapH ? intervalKo(gapH) : "미정"})만큼 비워 미래 값이 앞 구간에 섞이지 않게 합니다.
        </p>

        <fieldset className="checks metric-checks">
          <legend className="field-legend">평가 지표</legend>
          {METRICS.map((m) => (
            <label key={m.key} className="check">
              <input
                type="checkbox"
                checked={evaluation.metrics.includes(m.key)}
                onChange={(e) =>
                  edit(() =>
                    setEvaluation({
                      ...evaluation,
                      metrics: e.target.checked ? [...evaluation.metrics, m.key] : evaluation.metrics.filter((x) => x !== m.key),
                    }),
                  )
                }
              />
              {m.label} <span className="muted">{m.hint}</span>
            </label>
          ))}
        </fieldset>
        <Msgs issues={exact("evaluation.metrics").concat(exact("evaluation"))} />

        <div className="field baseline-field">
          <label htmlFor={ids.baseline}>기준 예측(비교용)</label>
          <select
            id={ids.baseline}
            value={evaluation.baseline}
            onChange={(e) => edit(() => setEvaluation({ ...evaluation, baseline: e.target.value as TsEvaluation["baseline"] }))}
          >
            <option value="naive_last_value">직전값 — 마지막 관측값을 그대로 예측</option>
            <option value="none">사용 안 함</option>
          </select>
          <Msgs issues={exact("evaluation.baseline")} />
        </div>

        <fieldset className="checks">
          <legend className="field-legend">지표 집계</legend>
          <label className="check">
            <input
              type="checkbox"
              checked={evaluation.per_horizon}
              onChange={(e) => edit(() => setEvaluation({ ...evaluation, per_horizon: e.target.checked }))}
            />
            예측 구간별 ({g.horizons.map((h) => intervalKo(h)).join(", ")})
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={evaluation.per_group}
              disabled={!g.group_col && !evaluation.per_group}
              onChange={(e) => edit(() => setEvaluation({ ...evaluation, per_group: e.target.checked }))}
            />
            설비별 {g.group_col ? `(${g.group_col})` : "— 설비 구분 열을 정하지 않아 사용할 수 없습니다"}
          </label>
        </fieldset>
        <Msgs issues={exact("evaluation.per_group").concat(exact("evaluation.per_horizon"))} />
      </section>

      <div className="save-bar">
        <p className="validate-summary" aria-live="polite">
          검증: {errs.length ? `확인 필요 ${errs.length}건` : "형식 문제 없음"}
          {notReady ? ", 실행 불가 항목 있음(계획 미리보기)" : ""}
          {dirty ? " · 저장하지 않은 변경 있음" : ""}
        </p>
        <div className="actions">
          {save.text && (
            <p className={save.kind === "error" ? "field-error" : "saved-text"} role="status">
              {save.text}
            </p>
          )}
          <a className="btn" href={preprocessHref}>
            ← 전처리
          </a>
          <button type="button" className="btn" disabled={save.kind === "saving" || !!conflict} onClick={() => void doSave()}>
            {save.kind === "saving" ? "저장 중" : "설정 저장"}
          </button>
          <button type="button" className="btn btn-primary" disabled={save.kind === "saving" || !!conflict} onClick={() => void goConfirm()}>
            {dirty ? "저장하고 확인·실행으로" : "다음: 확인·실행"}
          </button>
        </div>
      </div>
    </div>
  );
}
