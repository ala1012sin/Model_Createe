import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BackLink } from "../toast";
import { ApiError, api } from "../api";
import type { PlanView, RecommendationOut, StepIn, StepView, ValidationIssue } from "../types";
import { intervalKo } from "../format";
import { ParamEditor } from "./ParamEditor";
import type { EditGuard } from "../App";

type RecPhase =
  | { kind: "idle" }
  | { kind: "loading" }
  | { kind: "error"; message: string; code: string }
  | { kind: "confirm"; rec: RecommendationOut };

type Confirm = { kind: "manual" } | null;

const toIn = (s: StepView): StepIn => ({
  plugin_id: s.plugin_id,
  plugin_version: s.plugin_version,
  params: s.params,
  reason: s.reason,
});

function tone(s: StepView): "done" | "busy" | "fail" {
  return s.status === "ready" ? "done" : s.status === "unregistered" || s.status === "invalid" ? "fail" : "busy";
}

/** 체크를 다시 켤 때 stage 순서에 맞는 자리에 넣는다(고정 단계 앞뒤 유지). */
function insertByStage(list: StepView[], step: StepView): StepView[] {
  const st = step.stage ?? 50;
  const idx = list.findIndex((s) => (s.stage ?? 50) > st);
  return idx < 0 ? [...list, step] : [...list.slice(0, idx), step, ...list.slice(idx)];
}

export function PreprocessPage({
  datasetId,
  datasetsHref,
  trainHref,
  onGuard,
}: {
  datasetId: string;
  datasetsHref: string;
  trainHref: string;
  onGuard: (g: EditGuard | null) => void;
}) {
  const [plan, setPlan] = useState<PlanView | null>(null);
  const [loadError, setLoadError] = useState<{ code: string; message: string } | null>(null);
  const [steps, setSteps] = useState<StepView[]>([]);
  const [excluded, setExcluded] = useState<StepView[]>([]);
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [dirty, setDirty] = useState(false);
  const [selectedKey, setSelectedKey] = useState<string | null>(null);
  const [rec, setRec] = useState<RecPhase>({ kind: "idle" });
  const [confirm, setConfirm] = useState<Confirm>(null);
  const [conflict, setConflict] = useState<string | null>(null);
  const [save, setSave] = useState<{ kind: "idle" | "saving" | "saved" | "error"; text?: string }>({ kind: "idle" });
  const [addPick, setAddPick] = useState("");
  const autoTried = useRef(false);

  const adopt = useCallback((view: PlanView) => {
    setPlan(view);
    setSteps(view.steps);
    setExcluded(view.excluded);
    setIssues(view.issues);
    setDirty(false);
    setConflict(null);
    setSelectedKey((k) => (k && [...view.steps, ...view.excluded].some((s) => s.plugin_id === k) ? k : view.steps[0]?.plugin_id ?? null));
  }, []);

  const load = useCallback(async () => {
    try {
      adopt(await api.plan(datasetId));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof ApiError ? { code: e.body.code, message: e.message } : { code: "?", message: "불러오지 못했습니다" });
    }
  }, [datasetId, adopt]);

  useEffect(() => {
    autoTried.current = false;
    setPlan(null);
    void load();
  }, [load]);

  const handleConflict = (e: unknown): boolean => {
    if (e instanceof ApiError && e.body.code === "REVISION_CONFLICT") {
      setConflict(e.message);
      return true;
    }
    return false;
  };

  const applyRec = useCallback(
    async (r: RecommendationOut, revision: number) => {
      try {
        adopt(await api.applyRecommendation(datasetId, r.recommendation_id, revision));
        setRec({ kind: "idle" });
        setSave({ kind: "saved", text: "추천을 초안에 적용했습니다" });
      } catch (e) {
        if (!handleConflict(e))
          setRec({ kind: "error", code: e instanceof ApiError ? e.body.code : "?", message: e instanceof ApiError ? e.message : "적용하지 못했습니다" });
      }
    },
    [datasetId, adopt],
  );

  const recommend = useCallback(async () => {
    if (!plan) return;
    setRec({ kind: "loading" });
    try {
      const r = await api.recommend(datasetId);
      const hasWork = steps.length > 0 || excluded.length > 0;
      if (hasWork) setRec({ kind: "confirm", rec: r });
      else await applyRec(r, plan.revision);
    } catch (e) {
      setRec({ kind: "error", code: e instanceof ApiError ? e.body.code : "?", message: e instanceof ApiError ? e.message : "추천을 받지 못했습니다" });
    }
  }, [plan, datasetId, steps.length, excluded.length, applyRec]);

  // 목표만 저장된 새 초안이면 들어오자마자 한 번 추천을 받는다
  useEffect(() => {
    if (plan && !autoTried.current && plan.source === "goal" && !plan.recommendation && !plan.steps.length && !plan.excluded.length) {
      autoTried.current = true;
      void recommend();
    }
  }, [plan, recommend]);

  const doSave = async (opts?: { steps?: StepView[]; excluded?: StepView[]; source?: "manual" }): Promise<boolean> => {
    if (!plan) return false;
    setSave({ kind: "saving" });
    try {
      const view = await api.savePlan(datasetId, {
        expected_revision: plan.revision,
        steps: (opts?.steps ?? steps).map(toIn),
        excluded: (opts?.excluded ?? excluded).map(toIn),
        ...(opts?.source ? { source: opts.source } : {}),
      });
      adopt(view);
      setSave({ kind: "saved", text: "초안을 저장했습니다" });
      return true;
    } catch (e) {
      if (handleConflict(e)) {
        setSave({ kind: "idle" });
        return false;
      }
      if (e instanceof ApiError && e.body.issues) setIssues(e.body.issues);
      setSave({ kind: "error", text: e instanceof ApiError ? e.message : "저장하지 못했습니다" });
      return false;
    }
  };

  // 상단 검색 이동 전 저장 여부 확인에 쓰는 상태(App에 전달)
  const saveRef = useRef(doSave);
  saveRef.current = doSave;
  useEffect(() => {
    onGuard({ dirty, save: () => saveRef.current() });
  }, [dirty, onGuard]);
  useEffect(() => () => onGuard(null), [onGuard]);

  const goTraining = async () => {
    if (dirty && !(await doSave())) return;
    window.location.hash = trainHref;
  };

  const startManual = async () => {
    setConfirm(null);
    setRec({ kind: "idle" });
    await doSave({ steps: [], excluded: [], source: "manual" });
  };

  // 편집 후 0.5초 뒤 서버 초안 검증
  useEffect(() => {
    if (!dirty) return;
    const t = window.setTimeout(() => {
      api
        .validatePlan(datasetId, steps.map(toIn))
        .then((r) => setIssues(r.issues))
        .catch(() => undefined);
    }, 500);
    return () => window.clearTimeout(t);
  }, [dirty, steps, datasetId]);

  const edit = (nextSteps: StepView[], nextExcluded = excluded) => {
    setSteps(nextSteps);
    setExcluded(nextExcluded);
    setDirty(true);
    setSave({ kind: "idle" });
  };

  const stepIssues = (i: number) => issues.filter((x) => x.path === `preprocessing[${i}]` || x.path.startsWith(`preprocessing[${i}].`));
  const exIssues = (i: number) => issues.filter((x) => x.path.startsWith(`excluded_steps[${i}]`));
  const prepIssues = issues.filter((x) => x.path.startsWith("preprocessing[") || x.path.startsWith("excluded_steps["));
  const nErr = prepIssues.filter((x) => x.severity === "error").length;
  const nWarn = prepIssues.filter((x) => x.severity === "warning").length;
  const nNotReady = prepIssues.filter((x) => x.code === "PLUGIN_NOT_READY").length;

  const addable = useMemo(() => {
    const present = new Set([...steps, ...excluded].map((s) => s.plugin_id));
    return (plan?.catalog ?? []).filter((c) => !present.has(c.plugin_id));
  }, [plan, steps, excluded]);

  const selIndex = steps.findIndex((s) => s.plugin_id === selectedKey);
  const exIndex = excluded.findIndex((s) => s.plugin_id === selectedKey);
  const selected = selIndex >= 0 ? steps[selIndex] : exIndex >= 0 ? excluded[exIndex] : null;

  // --- 화면 ---------------------------------------------------------------
  if (loadError) {
    return (
      <div className="prep">
        <h1 className="page-title">전처리</h1>
        <div className="panel-empty" role="alert">
          <h2 className="panel-title">
            {loadError.code === "GOAL_REQUIRED" ? "예측 목표를 먼저 저장해 주세요" : "전처리 초안을 불러오지 못했습니다"}
          </h2>
          <p>
            {loadError.code === "GOAL_REQUIRED"
              ? "데이터셋 화면의 예측 목표에서 시간열과 예측 타깃, 예측 구간을 정하고 저장하면 추천을 받을 수 있습니다."
              : loadError.message}
          </p>
          <div className="actions">
            <a className="btn btn-primary" href={datasetsHref}>
              데이터셋 화면으로
            </a>
          </div>
        </div>
      </div>
    );
  }
  if (!plan) {
    return (
      <div className="prep" aria-busy="true">
        <h1 className="page-title">전처리</h1>
        <p className="muted">초안을 불러오는 중입니다.</p>
      </div>
    );
  }

  const g = plan.goal;
  const preview = plan.recommendation?.mode === "preview" || [...steps, ...excluded].some((s) => s.status !== "ready");

  return (
    <div className="prep">
      <BackLink href={datasetsHref} label="데이터셋·목표" />
      <h1 className="page-title">전처리</h1>
      <p className="goal-line">
        <strong>{plan.dataset_name}</strong>의 <strong>{g.target_col}</strong>을(를){" "}
        {g.horizons.map((h) => `${intervalKo(h)} 뒤`).join(", ")} 예측
        {g.group_col ? <> (설비 구분 {g.group_col})</> : null}. 시간열 {g.timestamp_col}.{" "}
        <a href={datasetsHref}>목표 바꾸기</a>
      </p>

      <div className="source-bar">
        <p>
          <span className="source-label">추천 출처</span>{" "}
          {plan.recommendation ? plan.recommendation.source_label : plan.source === "manual" ? "수동 편집" : "아직 추천을 받지 않았습니다"}
        </p>
        <div className="actions">
          <button type="button" className="btn" onClick={() => void recommend()} disabled={rec.kind === "loading"}>
            {rec.kind === "loading" ? "추천을 만드는 중" : plan.recommendation ? "다시 추천" : "Mock 추천 받기"}
          </button>
          <button
            type="button"
            className="btn btn-quiet"
            onClick={() => (steps.length || excluded.length ? setConfirm({ kind: "manual" }) : void startManual())}
          >
            수동으로 시작
          </button>
        </div>
      </div>

      {preview && (
        <p className="preview-note" role="note">
          계획 미리보기: 지금 등록된 전처리는 아직 구현 전이라 항목을 고르고 순서·파라미터를 정해 저장하는 것까지만 할 수 있습니다.
          이 초안은 승인·학습 실행에 쓸 수 없습니다.
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

      {rec.kind === "error" && (
        <div className="banner banner-fail" role="alert">
          <p>
            추천을 받지 못했습니다: {rec.message}
            <span className="muted"> ({rec.code})</span>
          </p>
          <div className="actions">
            <button type="button" className="btn btn-small" onClick={() => void recommend()}>
              다시 추천
            </button>
            <button type="button" className="btn btn-small btn-quiet" onClick={() => setRec({ kind: "idle" })}>
              닫고 직접 편집
            </button>
          </div>
        </div>
      )}

      {rec.kind === "confirm" && (
        <div className="banner" role="alertdialog" aria-labelledby="replace-title">
          <p id="replace-title">
            새 추천에 {rec.rec.steps.length}개 항목이 있습니다. 지금 편집안을 새 추천으로 바꿀까요?
          </p>
          <p className="muted">
            {rec.rec.steps.map((s) => s.display_name_ko).join(", ")}
            {rec.rec.model_name ? ` / 모델 후보: ${rec.rec.model_name}` : ""}
          </p>
          <div className="actions">
            <button type="button" className="btn btn-small btn-primary" onClick={() => void applyRec(rec.rec, plan.revision)}>
              추천으로 바꾸기
            </button>
            <button type="button" className="btn btn-small" onClick={() => setRec({ kind: "idle" })}>
              지금 편집안 유지
            </button>
          </div>
        </div>
      )}

      {confirm?.kind === "manual" && (
        <div className="banner" role="alertdialog" aria-labelledby="manual-title">
          <p id="manual-title">목록을 비우고 수동으로 시작할까요? 지금 항목은 모두 사라집니다.</p>
          <div className="actions">
            <button type="button" className="btn btn-small btn-primary" onClick={() => void startManual()}>
              비우고 수동으로 시작
            </button>
            <button type="button" className="btn btn-small" onClick={() => setConfirm(null)}>
              취소
            </button>
          </div>
        </div>
      )}

      {(plan.plugin_problems?.length ?? 0) > 0 && (
        <div className="banner banner-warn" role="status">
          <p>불러오지 못한 사용자 정의 플러그인이 있습니다(plugins 폴더):</p>
          <ul>
            {plan.plugin_problems!.map((p) => (
              <li key={p.folder}>
                {p.folder} — {p.message_ko}
              </li>
            ))}
          </ul>
        </div>
      )}

      {plan.skipped.length > 0 && (
        <div className="banner banner-fail" role="status">
          <p>추천 중 초안에 넣지 못한 항목:</p>
          <ul>
            {plan.skipped.map((s) => (
              <li key={s.plugin_id}>
                {s.plugin_id} — {s.reason_ko}
              </li>
            ))}
          </ul>
        </div>
      )}

      <div className="prep-grid">
        <section className="prep-list" aria-labelledby="order-title">
          <h2 className="section-title" id="order-title">
            실행 순서 <span className="count">체크한 항목만 위에서부터 실행</span>
          </h2>
          {steps.length === 0 && (
            <p className="empty-text">
              {plan.source === "manual" ? "수동 모드입니다. 아래에서 전처리를 추가하세요." : "실행할 전처리가 없습니다."}
            </p>
          )}
          <ol className="cards">
            {steps.map((s, i) => {
              const iss = stepIssues(i);
              const errs = iss.filter((x) => x.severity === "error");
              const ownErrs = errs.filter((x) => x.code !== "PLUGIN_NOT_READY");
              return (
                <li key={s.plugin_id} className={`card${selectedKey === s.plugin_id ? " is-selected" : ""}${ownErrs.length ? " has-error" : ""}`}>
                  <input
                    type="checkbox"
                    className="card-check"
                    checked
                    aria-label={`${s.display_name_ko} 실행에 포함`}
                    onChange={() => edit(steps.filter((_, j) => j !== i), [...excluded, s])}
                  />
                  <span className="card-no" aria-hidden="true">
                    {i + 1}
                  </span>
                  <button type="button" className="card-main" aria-pressed={selectedKey === s.plugin_id} onClick={() => setSelectedKey(s.plugin_id)}>
                    <span className="card-name">{s.display_name_ko}</span>
                    <span className="card-meta">
                      <span className={`status status-${tone(s)}`}>{s.status_label}</span>
                      {s.origin === "user" && <span className="lock user-badge">사용자 정의</span>}
                      {s.locked && <span className="lock">순서 고정</span>}
                      {ownErrs.length > 0 && <span className="card-err">확인 필요 {ownErrs.length}</span>}
                    </span>
                    {ownErrs.map((x, n) => (
                      <span key={n} className="card-issue">
                        {x.message_ko}
                      </span>
                    ))}
                  </button>
                  <span className="card-move">
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`${s.display_name_ko} 위로`}
                      disabled={i === 0 || s.locked}
                      title={s.locked ? "순서가 고정된 단계입니다" : undefined}
                      onClick={() => edit(steps.map((x, j) => (j === i - 1 ? s : j === i ? steps[i - 1] : x)))}
                    >
                      ▲
                    </button>
                    <button
                      type="button"
                      className="icon-btn"
                      aria-label={`${s.display_name_ko} 아래로`}
                      disabled={i === steps.length - 1 || s.locked}
                      title={s.locked ? "순서가 고정된 단계입니다" : undefined}
                      onClick={() => edit(steps.map((x, j) => (j === i + 1 ? s : j === i ? steps[i + 1] : x)))}
                    >
                      ▼
                    </button>
                  </span>
                </li>
              );
            })}
          </ol>

          {excluded.length > 0 && (
            <>
              <h3 className="sub-title">제외한 항목</h3>
              <ul className="cards cards-excluded">
                {excluded.map((s, i) => (
                  <li key={s.plugin_id} className={`card is-off${selectedKey === s.plugin_id ? " is-selected" : ""}`}>
                    <input
                      type="checkbox"
                      className="card-check"
                      checked={false}
                      aria-label={`${s.display_name_ko} 실행에 포함`}
                      onChange={() => edit(insertByStage(steps, s), excluded.filter((_, j) => j !== i))}
                    />
                    <span className="card-no" aria-hidden="true">
                      –
                    </span>
                    <button type="button" className="card-main" aria-pressed={selectedKey === s.plugin_id} onClick={() => setSelectedKey(s.plugin_id)}>
                      <span className="card-name">{s.display_name_ko}</span>
                      <span className="card-meta">
                        <span className={`status status-${tone(s)}`}>{s.status_label}</span>
                      </span>
                      {exIssues(i).map((x, n) => (
                        <span key={n} className="card-issue">
                          {x.message_ko}
                        </span>
                      ))}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}

          <div className="add-row">
            <label htmlFor="add-step" className="visually-hidden">
              추가할 전처리
            </label>
            <select id="add-step" value={addPick} onChange={(e) => setAddPick(e.target.value)}>
              <option value="">전처리 추가 (등록된 호환 항목)</option>
              {addable.map((c) => (
                <option key={c.plugin_id} value={c.plugin_id}>
                  {c.display_name_ko}
                  {c.origin === "user" ? " (사용자 정의)" : ""} — {c.status_label}
                </option>
              ))}
            </select>
            <button
              type="button"
              className="btn btn-small"
              disabled={!addPick}
              onClick={() => {
                const c = addable.find((x) => x.plugin_id === addPick);
                if (!c) return;
                edit(insertByStage(steps, { ...c, params: {}, reason: "" }));
                setSelectedKey(c.plugin_id);
                setAddPick("");
              }}
            >
              추가
            </button>
          </div>
        </section>

        <section className="prep-detail" aria-label="선택한 전처리">
          {selected ? (
            <DetailPanel
              step={selected}
              columns={plan.columns}
              issues={selIndex >= 0 ? stepIssues(selIndex) : exIssues(exIndex)}
              pathPrefix={selIndex >= 0 ? `preprocessing[${selIndex}].params` : `excluded_steps[${exIndex}].params`}
              onParams={(params) => {
                if (selIndex >= 0) edit(steps.map((x, j) => (j === selIndex ? { ...x, params } : x)));
                else edit(steps, excluded.map((x, j) => (j === exIndex ? { ...x, params } : x)));
              }}
            />
          ) : (
            <p className="muted">왼쪽에서 항목을 선택하면 설명과 파라미터가 여기에 표시됩니다.</p>
          )}
        </section>
      </div>

      <div className="save-bar">
        <p className="validate-summary" aria-live="polite">
          검증: {nErr - nNotReady > 0 ? `확인 필요 ${nErr - nNotReady}건` : "형식·순서 문제 없음"}
          {nWarn > 0 ? `, 경고 ${nWarn}건` : ""}
          {nNotReady > 0 ? `, 실행 불가 항목 ${nNotReady}개(계획 미리보기)` : ""}
          {dirty ? " · 저장하지 않은 변경 있음" : ""}
        </p>
        <div className="actions">
          {save.text && (
            <p className={save.kind === "error" ? "field-error" : "saved-text"} role="status">
              {save.text}
            </p>
          )}
          <button type="button" className="btn" disabled={save.kind === "saving" || !!conflict} onClick={() => void doSave()}>
            {save.kind === "saving" ? "저장 중" : "초안 저장"}
          </button>
          <button type="button" className="btn btn-primary" disabled={save.kind === "saving" || !!conflict} onClick={() => void goTraining()}>
            {dirty ? "저장하고 학습 설정으로" : "학습 설정으로"}
          </button>
        </div>
      </div>
    </div>
  );
}

function DetailPanel(props: {
  step: StepView;
  columns: PlanView["columns"];
  issues: ValidationIssue[];
  pathPrefix: string;
  onParams: (p: Record<string, unknown>) => void;
}) {
  const s = props.step;
  // 실행 불가(미구현)는 위 구현 상태 배지·사유로 이미 표시하므로 반복하지 않는다
  const general = props.issues.filter((x) => !x.path.startsWith(props.pathPrefix) && x.code !== "PLUGIN_NOT_READY");
  return (
    <div className="detail">
      <h2 className="panel-title">{s.display_name_ko}</h2>
      <p className="muted">
        {s.plugin_id}@{s.plugin_version}
        {s.origin === "user" ? " · 사용자 정의 플러그인(plugins 폴더의 로컬 코드)" : ""}
      </p>
      <h3 className="sub-title">무엇을 하나요</h3>
      <p>{s.description_ko}</p>
      <h3 className="sub-title">이 데이터에 추천한 이유</h3>
      {/* LLM이 쓴 문장은 설명용 일반 텍스트로만 표시(명령·링크로 렌더링하지 않음) */}
      <p className="reason">{s.reason || "추천 이유가 없습니다(직접 추가한 항목)."}</p>
      <h3 className="sub-title">구현 상태</h3>
      <p>
        <span className={`status status-${tone(s)}`}>{s.status_label}</span>{" "}
        {s.reason_unavailable && <span className="muted">{s.reason_unavailable}</span>}
      </p>
      {s.locked && <p className="muted">순서가 고정된 단계입니다. 다른 항목보다 앞에서 실행됩니다.</p>}
      {general.map((x, n) => (
        <p key={n} className={x.severity === "error" ? "field-error" : "field-warn"}>
          {x.message_ko}
          {x.fix_hint_ko ? ` — ${x.fix_hint_ko}` : ""}
        </p>
      ))}
      <h3 className="sub-title">파라미터</h3>
      {s.registered ? (
        <ParamEditor
          key={`${s.plugin_id}-${props.pathPrefix}`}
          schema={s.parameter_schema}
          params={s.params}
          columns={props.columns}
          issues={props.issues}
          pathPrefix={props.pathPrefix}
          onChange={props.onParams}
        />
      ) : (
        <p className="muted">등록되지 않은 도구라 편집할 수 없습니다.</p>
      )}
    </div>
  );
}
