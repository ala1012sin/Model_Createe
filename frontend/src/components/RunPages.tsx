import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { BackLink } from "../toast";
import { ComparePanel, SERIES_COLORS } from "./ComparePanel";
import { DeleteDialog } from "./DeleteDialog";
import { ApiError, api } from "../api";
import type { MetricBlock, RunStage, RunState, RunView, SplitMetrics, TrainBudget } from "../types";
import { intervalKo } from "../format";

export const RUN_STATE_KO: Record<RunState, string> = {
  QUEUED: "대기",
  RUNNING: "실행 중",
  CANCEL_REQUESTED: "취소 요청됨",
  SUCCEEDED: "완료",
  FAILED: "실패",
  CANCELED: "취소됨",
};
const TONE: Record<RunState, string> = {
  QUEUED: "status-busy",
  RUNNING: "status-busy",
  CANCEL_REQUESTED: "status-busy",
  SUCCEEDED: "status-done",
  FAILED: "status-fail",
  CANCELED: "status-fail",
};
const STAGES: { key: RunStage; label: string }[] = [
  { key: "queued", label: "대기" },
  { key: "prepare", label: "데이터 준비" },
  { key: "preprocess", label: "전처리" },
  { key: "train", label: "학습" },
  { key: "evaluate", label: "평가" },
  { key: "save", label: "저장" },
];
const EVENT_KO: Record<string, string> = {
  RUN_QUEUED: "대기열 등록",
  RUN_STARTED: "학습 프로세스 시작",
  PREPARE_STARTED: "데이터 준비 시작",
  PREPROCESS_STARTED: "전처리 시작",
  PREPROCESS_STEP_DONE: "전처리 단계 완료",
  PREPROCESS_DONE: "전처리·분할 완료",
  TRAIN_STARTED: "학습 시작",
  TRAIN_PROGRESS: "학습 진행",
  TRAIN_DONE: "학습 완료",
  EVALUATE_STARTED: "평가 시작",
  SAVE_STARTED: "결과 저장 시작",
  SAVE_DONE: "결과 저장 완료",
  RUN_SUCCEEDED: "완료",
  RUN_FAILED: "실패",
  RUN_CANCELED: "취소됨",
  CANCEL_REQUESTED: "취소 요청",
};
const TERMINAL: RunState[] = ["SUCCEEDED", "FAILED", "CANCELED"];
const MODEL_KO: Record<string, string> = { "timeseries.forecasting.xgboost": "XGBoost" };
const METRIC_KO: Record<string, string> = { mae: "MAE", rmse: "RMSE", r2: "R²", smape: "sMAPE(%)" };

const budgetKo = (b: TrainBudget | null | undefined) =>
  !b ? "—" : b.mode === "all" ? "전체" : b.mode === "recent_days" ? `최근 ${b.days}일` : "—";
const timeKo = (iso: string | null | undefined) => {
  if (!iso) return "";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleTimeString("ko-KR", { hour12: false });
};
const dataTime = (s: string | undefined) => (s ? s.replace("T", " ").replace(/:00$/, "") : "");
const fmt = (v: number | null | undefined) =>
  v === null || v === undefined ? "계산 불가" : Math.abs(v) >= 100 ? v.toFixed(1) : v.toFixed(3);

function elapsed(from: string | null, to: string | null, now: number): string {
  if (!from) return "";
  const end = to ? new Date(to).getTime() : now;
  const s = Math.max(0, Math.round((end - new Date(from).getTime()) / 1000));
  return `${String(Math.floor(s / 60)).padStart(2, "0")}:${String(s % 60).padStart(2, "0")}`;
}

function Badge({ state }: { state: RunState }) {
  return <span className={`status ${TONE[state]}`}>{RUN_STATE_KO[state]}</span>;
}

// --- 목록 = S6 비교 표 ---------------------------------------------------------------
const MAX_COMPARE = 6;
const durShort = (s: number | null | undefined) => (s == null ? "—" : s < 60 ? `${s}초` : `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`);

export function RunsPage({ trainHref }: { trainHref: string | null }) {
  const [items, setItems] = useState<RunView[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [selected, setSelected] = useState<string[]>([]);
  const [dataset, setDataset] = useState("");
  const [sameOnly, setSameOnly] = useState(false);
  const [deleting, setDeleting] = useState<RunView | null>(null);
  const [tick, setTick] = useState(0);
  useEffect(() => {
    let alive = true;
    const load = () =>
      api
        .runs()
        .then((r) => alive && setItems(r.items))
        .catch((e) => alive && setError(e instanceof ApiError ? e.message : "불러오지 못했습니다"));
    void load();
    const t = window.setInterval(load, 3000);
    return () => {
      alive = false;
      window.clearInterval(t);
    };
  }, [tick]);

  const datasets = useMemo(() => {
    const m = new Map<string, string>();
    (items ?? []).forEach((r) => m.set(r.dataset_id, r.dataset_name ?? r.dataset_id));
    return [...m.entries()];
  }, [items]);
  const refRun = (items ?? []).find((r) => r.run_id === selected[0]);
  const refKey = refRun?.protocol_key ?? null;
  const shown = (items ?? []).filter(
    (r) => (!dataset || r.dataset_id === dataset) && (!sameOnly || !refKey || r.protocol_key === refKey),
  );
  const toggle = (id: string) =>
    setSelected((cur) => (cur.includes(id) ? cur.filter((x) => x !== id) : cur.length >= MAX_COMPARE ? cur : [...cur, id]));
  const live = selected.filter((id) => (items ?? []).some((r) => r.run_id === id && r.state === "SUCCEEDED"));

  return (
    <div className="runs">
      <h1 className="page-title">실행·결과</h1>
      {error && <p className="field-error">{error}</p>}
      {items && items.length === 0 && (
        <div className="panel-empty">
          <h2 className="panel-title">실행한 학습이 없습니다</h2>
          <p>학습 설정을 저장하고 확인·실행 화면에서 승인한 뒤 학습을 시작하세요.</p>
          {trainHref && (
            <div className="actions">
              <a className="btn btn-primary" href={trainHref}>
                학습 설정으로
              </a>
            </div>
          )}
        </div>
      )}
      {items && items.length > 0 && (
        <>
          <div className="cmp-controls runs-toolbar">
            <label>
              데이터셋{" "}
              <select value={dataset} onChange={(e) => setDataset(e.target.value)}>
                <option value="">전체</option>
                {datasets.map(([id, name]) => (
                  <option key={id} value={id}>
                    {name}
                  </option>
                ))}
              </select>
            </label>
            <label className="check">
              <input type="checkbox" checked={sameOnly} disabled={!refKey} onChange={(e) => setSameOnly(e.target.checked)} /> 기준과
              같은 평가 조건만
            </label>
            <span className="muted small">
              {selected.length === 0
                ? `완료된 실행을 2~${MAX_COMPARE}개 체크하면 아래에 비교가 나옵니다(처음 고른 실행이 기준).`
                : `선택 ${selected.length}개 · 기준 ${selected[0]}`}
            </span>
            {selected.length > 0 && (
              <button type="button" className="btn btn-small btn-quiet" onClick={() => setSelected([])}>
                선택 해제
              </button>
            )}
          </div>
          <div className="table-wrap">
            <table className="run-table">
              <thead>
                <tr>
                  <th scope="col">
                    <span className="visually-hidden">비교 선택</span>
                  </th>
                  <th scope="col">실행</th>
                  <th scope="col">데이터셋</th>
                  <th scope="col">모델</th>
                  <th scope="col">전처리</th>
                  <th scope="col">학습량</th>
                  <th scope="col">예측</th>
                  <th scope="col">테스트 MAE</th>
                  <th scope="col">테스트 RMSE</th>
                  <th scope="col">직전값 대비 MAE</th>
                  <th scope="col">소요</th>
                  <th scope="col">상태</th>
                  <th scope="col">
                    <span className="visually-hidden">관리</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {shown.map((r) => {
                  const t = r.summary?.test;
                  const mae = t?.model.mae;
                  const base = t?.baseline?.mae;
                  const rel = mae != null && base ? Math.round(((mae - base) / base) * 100) : null;
                  const idx = selected.indexOf(r.run_id);
                  const differs = refKey !== null && r.protocol_key != null && r.protocol_key !== refKey;
                  return (
                    <tr key={r.run_id} className={idx >= 0 ? "is-current" : undefined}>
                      <td>
                        <input
                          type="checkbox"
                          aria-label={`${r.run_id} 비교에 포함`}
                          checked={idx >= 0}
                          disabled={r.state !== "SUCCEEDED" || (idx < 0 && selected.length >= MAX_COMPARE)}
                          onChange={() => toggle(r.run_id)}
                        />
                        {idx >= 0 && <SelSwatch i={idx} />}
                      </td>
                      <td>
                        {r.name && <div className="run-name">{r.name}</div>}
                        <a href={`#/runs/${encodeURIComponent(r.run_id)}`}>{r.run_id}</a>
                        {idx === 0 && <span className="lock"> 기준</span>}
                        {differs && (
                          <span className="cmp-flag" title="기준 실행과 평가 조건(데이터·분할·테스트 표본·지표)이 달라 지표를 직접 비교할 수 없습니다">
                            {" "}
                            ⚠ 평가 조건 다름
                          </span>
                        )}
                        <div className="muted small">{new Date(r.created_at).toLocaleString("ko-KR")}</div>
                      </td>
                      <td className="col-ds">{r.dataset_name ?? r.dataset_id}</td>
                      <td className="nowrap">{MODEL_KO[r.model ?? ""] ?? r.model}</td>
                      <td className="small col-steps">{(r.steps ?? []).join(" → ") || "—"}</td>
                      <td className="nowrap">{budgetKo(r.train_budget)}</td>
                      <td className="small nowrap">{r.horizons.map((h) => intervalKo(h)).join(", ")}</td>
                      <td className="num">{mae != null ? fmt(mae) : "—"}</td>
                      <td className="num">{t?.model.rmse != null ? fmt(t.model.rmse) : "—"}</td>
                      <td className="num">{rel === null ? "—" : `${rel > 0 ? "+" : ""}${rel}%`}</td>
                      <td className="num">{durShort(r.duration_s)}</td>
                      <td className="nowrap">
                        <Badge state={r.state} />
                        {r.queue_position != null && <span className="muted small"> {r.queue_position}번째</span>}
                      </td>
                      <td>
                        {TERMINAL.includes(r.state) && (
                          <button type="button" className="btn btn-small btn-quiet" aria-label={`${r.name ?? r.run_id} 삭제`}
                            onClick={() => setDeleting(r)}>
                            삭제
                          </button>
                        )}
                      </td>
                    </tr>
                  );
                })}
              </tbody>
            </table>
          </div>
          {live.length >= 2 && <ComparePanel runIds={live} />}
        </>
      )}
      {deleting && (
        <DeleteDialog
          target={{ kind: "run", id: deleting.run_id, label: deleting.name ?? deleting.run_id }}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            setSelected((cur) => cur.filter((x) => x !== deleting.run_id));
            setDeleting(null);
            setTick((n) => n + 1);
          }}
        />
      )}
    </div>
  );
}

function SelSwatch({ i }: { i: number }) {
  return <span className="swatch" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} aria-hidden="true" />;
}

// --- 상세 ------------------------------------------------------------------------
function MetricsTable({ m, metrics, title }: { m: SplitMetrics; metrics: string[]; title: string }) {
  const rows: [string, MetricBlock][] = [["전체", m.overall]];
  for (const [h, b] of Object.entries(m.per_horizon ?? {})) rows.push([`${intervalKo(h)} 뒤`, b]);
  for (const [g, b] of Object.entries(m.per_group ?? {})) rows.push([`설비 ${g}`, b]);
  const hasBase = !!m.overall.baseline;
  return (
    <div className="table-wrap">
      <table className="metric-table">
        <caption>{title}</caption>
        <thead>
          <tr>
            <th scope="col">구분</th>
            <th scope="col" className="num">
              표본
            </th>
            {metrics.map((k) => (
              <th key={k} scope="col" className="num">
                {METRIC_KO[k] ?? k}
                {hasBase ? " (모델 / 직전값)" : ""}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map(([label, b]) => (
            <tr key={label}>
              <th scope="row">{label}</th>
              <td className="num">{b.n.toLocaleString("ko-KR")}</td>
              {metrics.map((k) => (
                <td key={k} className="num">
                  <strong>{fmt(b.model[k])}</strong>
                  {b.baseline && <span className="muted"> / {fmt(b.baseline[k])}</span>}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function RunPage({ runId }: { runId: string }) {
  const [run, setRun] = useState<RunView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [now, setNow] = useState(Date.now());
  const [confirmCancel, setConfirmCancel] = useState(false);
  const [busy, setBusy] = useState(false);
  const [editName, setEditName] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const [deleting, setDeleting] = useState(false);
  const timer = useRef<number | null>(null);
  const saveName = async () => {
    try {
      setRun(await api.renameRun(runId, editName?.trim() || null));
      setEditName(null);
      setNameError(null);
    } catch (e) {
      setNameError(e instanceof ApiError ? e.message : "이름을 저장하지 못했습니다");
    }
  };

  const load = useCallback(async () => {
    try {
      const r = await api.run(runId);
      setRun(r);
      setError(null);
      return r;
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "불러오지 못했습니다");
      return null;
    }
  }, [runId]);

  useEffect(() => {
    let alive = true;
    setRun(null);
    const tick = async () => {
      const r = await load();
      setNow(Date.now());
      if (alive && (!r || !TERMINAL.includes(r.state))) timer.current = window.setTimeout(tick, 1000);
    };
    void tick();
    return () => {
      alive = false;
      if (timer.current) window.clearTimeout(timer.current);
    };
  }, [load]);

  const cancel = async () => {
    setBusy(true);
    try {
      setRun(await api.cancelRun(runId));
      setConfirmCancel(false);
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "취소 요청을 보내지 못했습니다");
    } finally {
      setBusy(false);
    }
  };

  if (!run) {
    return (
      <div className="run" aria-busy={!error}>
        <h1 className="page-title">실행·결과</h1>
        {error ? <p className="field-error">{error}</p> : <p className="muted">불러오는 중입니다.</p>}
      </div>
    );
  }
  const stageIdx = STAGES.findIndex((s) => s.key === run.stage);
  const done = run.state === "SUCCEEDED";
  const live = !TERMINAL.includes(run.state);
  const metrics = run.metrics_meta ? Object.keys(run.metrics_meta.definitions) : ["mae", "rmse"];
  const sp = run.split;
  const events = run.events.filter((e, i, a) => e.message_code !== "TRAIN_PROGRESS" || a[i + 1]?.message_code !== "TRAIN_PROGRESS");

  return (
    <div className="run">
      <p className="crumb">
        <BackLink href="#/runs" label="실행·결과 목록" /> <span className="muted">· {run.run_id}</span>
      </p>
      <h1 className="page-title">
        {run.name && <span className="run-name">{run.name} — </span>}
        {run.dataset_name ?? run.dataset_id} · {MODEL_KO[run.model ?? ""] ?? run.model} · {budgetKo(run.train_budget)}
      </h1>
      {editName === null ? (
        <p className="run-name-row">
          <button type="button" className="btn btn-small btn-quiet" onClick={() => setEditName(run.name ?? "")}>
            {run.name ? "이름 바꾸기" : "이름 붙이기"}
          </button>
          {TERMINAL.includes(run.state) && (
            <button type="button" className="btn btn-small btn-quiet" onClick={() => setDeleting(true)}>
              삭제…
            </button>
          )}
        </p>
      ) : (
        <form
          className="run-name-row"
          onSubmit={(e) => {
            e.preventDefault();
            void saveName();
          }}
        >
          <label className="visually-hidden" htmlFor="run-name">
            실행 이름
          </label>
          <input id="run-name" value={editName} maxLength={60} autoFocus placeholder="예: lag만 · 최근 30일"
            onChange={(e) => setEditName(e.target.value)} />
          <button type="submit" className="btn btn-small btn-primary">
            이름 저장
          </button>
          <button type="button" className="btn btn-small" onClick={() => setEditName(null)}>
            취소
          </button>
          {nameError && <span className="field-error">{nameError}</span>}
        </form>
      )}
      {deleting && (
        <DeleteDialog
          target={{ kind: "run", id: run.run_id, label: run.name ?? run.run_id }}
          onClose={() => setDeleting(false)}
          onDeleted={() => {
            window.location.hash = "#/runs";
          }}
        />
      )}
      <div className="run-head">
        <Badge state={run.state} />
        <span className="muted">
          예측 {run.horizons.map((h) => `${intervalKo(h)} 뒤`).join(", ")} · 경과 {elapsed(run.started_at ?? run.created_at, run.finished_at, now)}
        </span>
        {live && run.state !== "CANCEL_REQUESTED" && (
          <button type="button" className="btn btn-small" onClick={() => setConfirmCancel(true)}>
            취소…
          </button>
        )}
      </div>
      {run.queue_position != null && (
        <div className="banner banner-warn queue-note" role="status">
          <p>
            <strong>대기 중</strong> · 앞에 {run.queue_ahead}개 실행이 있습니다. 학습은 동시에 {run.max_parallel ?? 1}개씩
            제출 순서대로 진행되며, 앞 실행이 끝나면 자동으로 시작합니다. 앱을 다시 켜도 대기열은 유지됩니다.
          </p>
        </div>
      )}
      {confirmCancel && (
        <div className="banner banner-warn" role="alertdialog" aria-labelledby="cancel-title">
          <p id="cancel-title">
            {run.queue_position != null ? "대기 중인 학습을 취소합니다(대기열에서 빠집니다)." : "진행 중인 학습을 취소합니다. 저장된 중간 결과는 없습니다."}
          </p>
          <div className="actions">
            <button type="button" className="btn btn-small btn-primary" disabled={busy} onClick={() => void cancel()}>
              학습 취소
            </button>
            <button type="button" className="btn btn-small btn-quiet" onClick={() => setConfirmCancel(false)}>
              계속 학습
            </button>
          </div>
        </div>
      )}
      {error && <p className="field-error">{error}</p>}

      <ol className="stepper" aria-label="실행 단계">
        {STAGES.map((s, i) => {
          const cls =
            done || i < stageIdx ? "is-done" : i === stageIdx ? (run.state === "FAILED" ? "is-fail" : live ? "is-current" : "is-stop") : "";
          return (
            <li key={s.key} className={cls} aria-current={i === stageIdx && live ? "step" : undefined}>
              {s.label}
            </li>
          );
        })}
      </ol>
      {live && (
        <div className="run-progress">
          <p>
            현재 단계: <strong>{STAGES[stageIdx]?.label}</strong>
            {run.progress !== null && ` ${Math.round(run.progress * 100)}%`}
          </p>
          {run.progress !== null && (
            <div className="bar" role="progressbar" aria-valuemin={0} aria-valuemax={100} aria-valuenow={Math.round(run.progress * 100)}>
              <span style={{ width: `${Math.round(run.progress * 100)}%` }} />
            </div>
          )}
        </div>
      )}
      {run.state === "FAILED" && (
        <div className="banner banner-fail" role="alert">
          <p>
            <strong>{STAGES[stageIdx]?.label} 단계에서 실패했습니다.</strong> {run.error_message_ko}
          </p>
          <p className="muted small">오류 코드 {run.error_code} · 자세한 내용은 실행 폴더의 train.log에 있습니다.</p>
        </div>
      )}
      {run.state === "CANCELED" && (
        <div className="banner banner-warn" role="status">
          <p>{run.error_message_ko ?? "취소되었습니다."}</p>
        </div>
      )}

      {done && run.metrics?.test && (
        <section className="tsec" aria-labelledby="res-title">
          <h2 className="sub-title" id="res-title">
            결과 — 테스트 구간
          </h2>
          <MetricsTable m={run.metrics.test} metrics={metrics} title="테스트 지표 (모델 / 직전값 기준 예측)" />
          {run.metrics.val && (
            <details className="more">
              <summary>검증 구간 지표</summary>
              <MetricsTable m={run.metrics.val} metrics={metrics} title="검증 지표" />
            </details>
          )}
          <div className="actions">
            {run.predictions_available && (
              <a className="btn" href={api.predictionsHref(run.run_id)} download>
                평가 예측값 CSV 내려받기
              </a>
            )}
            <a className="btn btn-primary" href={`#/apply/${encodeURIComponent(run.run_id)}`}>
              이 모델로 새 데이터에 적용 →
            </a>
          </div>
        </section>
      )}
      {done && sp && (
        <section className="tsec" aria-labelledby="split-title">
          <h2 className="sub-title" id="split-title">
            실제 분할·학습 기간
          </h2>
          <dl className="conf-summary">
            <dt>학습</dt>
            <dd>
              {sp.train_period ? `${dataTime(sp.train_period[0])} ~ ${dataTime(sp.train_period[1])}` : "—"} ·{" "}
              {sp.train_samples.toLocaleString("ko-KR")}개 표본 ({budgetKo(sp.budget)})
            </dd>
            <dt>검증</dt>
            <dd>
              {sp.val_period ? `${dataTime(sp.val_period[0])} ~ ${dataTime(sp.val_period[1])}` : "없음"} ·{" "}
              {sp.val_samples.toLocaleString("ko-KR")}개
            </dd>
            <dt>테스트</dt>
            <dd>
              {sp.test_period ? `${dataTime(sp.test_period[0])} ~ ${dataTime(sp.test_period[1])}` : "—"} ·{" "}
              {sp.test_samples.toLocaleString("ko-KR")}개
            </dd>
            <dt>경계</dt>
            <dd>
              학습 ≤ {dataTime(sp.train_end)} &lt; 검증 ≤ {dataTime(sp.val_end)} &lt; 테스트 (데이터 {dataTime(sp.data_start)} ~{" "}
              {dataTime(sp.data_end)})
            </dd>
            <dt>규칙</dt>
            <dd className="muted small">
              {sp.gap_policy}. {sp.budget_policy}. {run.metrics_meta?.protocol}.
            </dd>
            {sp.notes.length > 0 && (
              <>
                <dt>전처리</dt>
                <dd className="muted small">{sp.notes.join(" · ")}</dd>
              </>
            )}
          </dl>
        </section>
      )}

      <section className="tsec" aria-labelledby="log-title">
        <h2 className="sub-title" id="log-title">
          이벤트 (실행기 기준)
        </h2>
        <ol className="event-log">
          {events.map((e) => (
            <li key={e.seq}>
              <time>{timeKo(e.occurred_at)}</time> {EVENT_KO[e.message_code] ?? e.message_code}
              {e.progress !== null && e.message_code === "TRAIN_PROGRESS" ? ` ${Math.round(e.progress * 100)}%` : ""}
            </li>
          ))}
        </ol>
        <p className="muted small">
          계획 해시 <code title={run.plan_hash}>{run.plan_hash.slice(0, 19)}…</code> · 실행 폴더 workspace/experiments/{run.run_id}
        </p>
      </section>
    </div>
  );
}
