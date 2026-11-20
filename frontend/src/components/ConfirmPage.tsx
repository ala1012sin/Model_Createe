import { useCallback, useEffect, useId, useState } from "react";
import { BackLink } from "../toast";
import { ApiError, api } from "../api";
import type { ConfirmationView, IssueView, SummaryStep } from "../types";
import { intervalKo } from "../format";

const STEP_LABEL: Record<NonNullable<IssueView["step"]>, string> = {
  goal: "데이터셋·목표에서 고치기",
  preprocess: "전처리에서 고치기",
  train: "학습 설정에서 고치기",
};
const METRIC_LABEL: Record<string, string> = { mae: "MAE", rmse: "RMSE", r2: "R²", smape: "sMAPE" };
const BASELINE_LABEL: Record<string, string> = {
  naive_last_value: "직전값",
  seasonal_naive: "계절 직전값",
  none: "사용 안 함",
};

const pct = (x: number) => `${Math.round(x * 1000) / 10}`;
// 승인 시각(UTC 저장)을 이 PC의 현지 시각으로 표시
const localTime = (iso: string | null) => {
  const d = iso ? new Date(iso) : null;
  return d && !Number.isNaN(d.getTime())
    ? d.toLocaleString("ko-KR", { year: "numeric", month: "2-digit", day: "2-digit", hour: "2-digit", minute: "2-digit" })
    : "";
};
const shortHash = (h: string) => h.replace(/^(sha256:)?(.{12}).*$/, "$1$2…");

function params(p: Record<string, unknown>): string {
  const parts = Object.entries(p).map(([k, v]) => `${k}=${typeof v === "object" ? JSON.stringify(v) : String(v)}`);
  return parts.join(", ");
}

function StepLine({ s, n }: { s: SummaryStep; n?: number }) {
  return (
    <li>
      {n !== undefined && <span className="conf-num">{n}</span>}
      <strong>{s.display_name_ko}</strong>
      <span className="muted"> {s.plugin_id}@{s.plugin_version}</span>
      {Object.keys(s.params).length > 0 && <span className="conf-params"> {params(s.params)}</span>}
      {s.status !== "ready" && <span className="conf-status">{s.status_label}</span>}
    </li>
  );
}

export function ConfirmPage({
  datasetId,
  hrefs,
}: {
  datasetId: string;
  hrefs: { goal: string; preprocess: string; train: string };
}) {
  const checkId = useId();
  const [view, setView] = useState<ConfirmationView | null>(null);
  const [loadError, setLoadError] = useState<{ code: string; message: string } | null>(null);
  const [checked, setChecked] = useState(false);
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState<{ kind: "error" | "conflict"; text: string } | null>(null);
  const [starting, setStarting] = useState(false);

  const load = useCallback(async () => {
    setNotice(null);
    try {
      setView(await api.confirmation(datasetId));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof ApiError ? { code: e.body.code, message: e.message } : { code: "?", message: "불러오지 못했습니다" });
    }
  }, [datasetId]);

  useEffect(() => {
    setView(null);
    setChecked(false);
    void load();
  }, [load]);

  const approve = async () => {
    if (!view) return;
    setBusy(true);
    setNotice(null);
    try {
      setView(await api.approve(datasetId, view.revision));
      setChecked(false);
    } catch (e) {
      if (e instanceof ApiError && e.status === 409) setNotice({ kind: "conflict", text: e.message });
      else {
        setNotice({ kind: "error", text: e instanceof ApiError ? e.message : "승인하지 못했습니다" });
        if (e instanceof ApiError && e.status === 422) setView(await api.confirmation(datasetId).catch(() => view));
      }
    } finally {
      setBusy(false);
    }
  };

  const [runName, setRunName] = useState("");
  const startRun = async () => {
    if (!view?.approval.plan_id || !view.approval.plan_hash) return;
    setStarting(true);
    setNotice(null);
    try {
      const run = await api.startRun(datasetId, view.approval.plan_id, view.approval.plan_hash, runName);
      window.location.hash = `#/runs/${encodeURIComponent(run.run_id)}`;
    } catch (e) {
      setNotice({ kind: e instanceof ApiError && e.status === 409 ? "conflict" : "error",
                  text: e instanceof ApiError ? e.message : "학습을 시작하지 못했습니다" });
      setStarting(false);
    }
  };

  if (loadError) {
    return (
      <div className="confirm">
        <h1 className="page-title">확인·실행</h1>
        <div className="panel-empty" role="alert">
          <h2 className="panel-title">
            {loadError.code === "GOAL_REQUIRED" ? "예측 목표를 먼저 저장해 주세요" : "설정을 불러오지 못했습니다"}
          </h2>
          <p>{loadError.code === "GOAL_REQUIRED" ? "데이터셋 화면에서 예측 목표를 저장한 뒤 전처리·학습 설정을 정해 주세요." : loadError.message}</p>
          <div className="actions">
            <a className="btn btn-primary" href={hrefs.goal}>
              데이터셋 화면으로
            </a>
          </div>
        </div>
      </div>
    );
  }
  if (!view) {
    return (
      <div className="confirm" aria-busy="true">
        <h1 className="page-title">확인·실행</h1>
        <p className="muted">설정 검증 중입니다.</p>
      </div>
    );
  }

  const g = view.goal;
  const a = view.approval;
  const errors = view.issues.filter((i) => i.severity === "error");
  const others = view.issues.filter((i) => i.severity !== "error");
  const sp = view.split_policy;
  const ev = view.evaluation;
  const approved = a.status === "approved";

  return (
    <div className="confirm">
      <BackLink href={hrefs.train} label="학습 설정" />
      <h1 className="page-title">확인·실행</h1>

      {view.error_count > 0 ? (
        <div className="banner banner-fail conf-head" role="status">
          <p>
            <strong>실행을 막는 오류 {view.error_count}건</strong>
            {view.warning_count > 0 ? ` · 경고 ${view.warning_count}건` : ""} — 아래 항목을 고친 뒤 승인할 수 있습니다.
          </p>
        </div>
      ) : view.warning_count > 0 ? (
        <div className="banner banner-warn conf-head" role="status">
          <p>
            <strong>경고 {view.warning_count}건 (승인 가능)</strong> — 내용을 확인하고 승인해 주세요.
          </p>
        </div>
      ) : (
        <div className="banner conf-head conf-ok" role="status">
          <p>
            <strong>검증 통과</strong> — 실행을 막는 오류가 없습니다.
          </p>
        </div>
      )}
      {a.status === "invalidated" && (
        <div className="banner banner-warn" role="alert">
          <p>실행 설정이 바뀌어 이전 승인이 무효화되었습니다. 다시 확인하고 승인해 주세요.</p>
        </div>
      )}
      {notice && (
        <div className={`banner ${notice.kind === "conflict" ? "banner-warn" : "banner-fail"}`} role="alert">
          <p>{notice.text}</p>
          {notice.kind === "conflict" && (
            <button type="button" className="btn btn-small" onClick={() => void load()}>
              최신 상태 불러오기
            </button>
          )}
        </div>
      )}

      {view.issues.length > 0 && (
        <section className="tsec conf-issues" aria-labelledby="conf-issues-title">
          <h2 className="sub-title" id="conf-issues-title">
            확인할 항목
          </h2>
          <ul>
            {[...errors, ...others].map((i, n) => (
              <li key={n} className={i.severity === "error" ? "is-error" : "is-warn"}>
                <span className={`conf-sev ${i.severity === "error" ? "status-fail" : "status-busy"}`}>
                  {i.severity === "error" ? "오류" : "경고"}
                </span>
                <span>
                  {i.message_ko}
                  {i.fix_hint_ko ? <span className="muted"> — {i.fix_hint_ko}</span> : null}
                </span>
                {i.step && (
                  <a className="conf-fix" href={hrefs[i.step]}>
                    {STEP_LABEL[i.step]} →
                  </a>
                )}
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="tsec" aria-labelledby="conf-sum-title">
        <h2 className="sub-title" id="conf-sum-title">
          계획 요약
        </h2>
        <dl className="conf-summary">
          <dt>데이터셋</dt>
          <dd>
            <strong>{view.dataset_name}</strong>
            {view.dataset_rows !== null && <> · {view.dataset_rows.toLocaleString("ko-KR")}행</>}
            {view.dataset_version && (
              <span className="muted" title={view.dataset_version}>
                {" "}
                · 버전 {shortHash(view.dataset_version)}
              </span>
            )}
          </dd>
          <dt>목표</dt>
          <dd>
            타깃 {g.target_col} · 시간열 {g.timestamp_col}
            {g.group_col ? ` · 설비 ${g.group_col}` : ""} · 예측 {g.horizons.map((h) => `${intervalKo(h)} 뒤`).join(", ")}{" "}
            <a href={hrefs.goal}>바꾸기</a>
          </dd>
          <dt>전처리</dt>
          <dd>
            {view.preprocessing.length ? (
              <ol className="conf-steps">
                {view.preprocessing.map((s, n) => (
                  <StepLine key={s.plugin_id} s={s} n={n + 1} />
                ))}
              </ol>
            ) : (
              "없음"
            )}
            {view.excluded.length > 0 && <p className="muted">제외: {view.excluded.join(", ")}</p>}
            <a href={hrefs.preprocess}>바꾸기</a>
          </dd>
          <dt>모델</dt>
          <dd>
            {view.model ? (
              <ul className="conf-steps">
                <StepLine s={view.model} />
              </ul>
            ) : (
              <span className="field-error">선택 안 됨</span>
            )}{" "}
            <a href={hrefs.train}>바꾸기</a>
          </dd>
          <dt>학습량</dt>
          <dd>
            {view.train_budget.mode === "all" ? "전체 기간" : `최근 ${view.train_budget.days ?? "?"}일`}
            {view.train_period || view.train_rows !== null ? (
              <>
                {" "}
                ({view.train_period}
                {view.train_rows !== null ? `, ${view.train_rows.toLocaleString("ko-KR")}행` : ""})
              </>
            ) : (
              <span className="muted"> · 실제 기간·행 수는 학습 준비 단계에서 계산합니다</span>
            )}
          </dd>
          <dt>분할·평가</dt>
          <dd>
            {sp ? `시간순 ${pct(sp.train)}/${pct(sp.val)}/${pct(sp.test)} · 간격 ${sp.gap === "auto" ? "자동" : sp.gap}` : "분할 미설정"}
            {ev ? (
              <>
                {" · "}
                {ev.metrics.map((m) => METRIC_LABEL[m] ?? m).join(", ")} · 기준 예측 {BASELINE_LABEL[ev.baseline] ?? ev.baseline}
                {ev.per_group ? " · 설비별" : ""}
                {ev.per_horizon ? " · 예측 시점별" : ""}
              </>
            ) : (
              " · 평가 미설정"
            )}
          </dd>
          <dt>실행 환경</dt>
          <dd className="muted">{view.estimated_duration ?? "예상 소요 시간은 학습 실행이 연결되면 표시합니다"}</dd>
          <dt>승인 상태</dt>
          <dd className="conf-approval">
            {approved ? (
              <>
                <span className="status status-done">승인됨</span> 계획 해시{" "}
                <code title={a.plan_hash ?? ""}>{shortHash(a.plan_hash ?? "")}</code> · {localTime(a.approved_at)}
              </>
            ) : a.status === "submitted" ? (
              <>
                <span className="status status-done">학습 시작함</span> 계획 해시{" "}
                <code title={a.plan_hash ?? ""}>{shortHash(a.plan_hash ?? "")}</code> ·{" "}
                <a href={`#/runs/${encodeURIComponent(a.run_id ?? "")}`}>실행 결과 보기</a>
                <span className="muted small"> (같은 설정으로 다시 학습하려면 다시 승인)</span>
              </>
            ) : a.status === "invalidated" ? (
              <span className="status status-busy">다시 승인 필요</span>
            ) : (
              <span className="muted">승인 전</span>
            )}
          </dd>
        </dl>
      </section>

      <div className="save-bar conf-bar">
        <label className="conf-check" htmlFor={checkId}>
          <input
            id={checkId}
            type="checkbox"
            checked={checked}
            disabled={!view.can_approve || busy}
            onChange={(e) => setChecked(e.target.checked)}
          />
          위 설정으로 승인합니다. (승인 후 실행 설정을 바꾸면 다시 승인해야 합니다)
        </label>
        <div className="actions">
          <label className="run-name-field">
            <span className="muted small">실행 이름 (선택)</span>
            <input value={runName} maxLength={60} placeholder="예: lag만 · 최근 30일" disabled={!view.run_available || starting}
              onChange={(e) => setRunName(e.target.value)} />
          </label>
          <a className="btn" href={hrefs.train}>
            ← 학습 설정
          </a>
          <button
            type="button"
            className={`btn${approved ? "" : " btn-primary"}`}
            disabled={!view.can_approve || !checked || busy}
            title={view.error_count ? "실행을 막는 오류를 먼저 고쳐 주세요" : approved ? "이미 승인했습니다" : undefined}
            onClick={() => void approve()}
          >
            {busy ? "승인 중" : approved ? "승인됨" : "설정 승인"}
          </button>
          <button
            type="button"
            className={`btn${approved ? " btn-primary" : ""}`}
            disabled={!view.run_available || starting}
            title={view.run_available ? undefined : approved ? "선택한 모델을 실행할 수 없습니다" : "설정을 승인하면 학습을 시작할 수 있습니다"}
            onClick={() => void startRun()}
          >
            {starting ? "시작 중" : "학습 시작 ▶"}
          </button>
        </div>
      </div>
    </div>
  );
}
