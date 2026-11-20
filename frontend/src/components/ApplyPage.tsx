import { useCallback, useEffect, useRef, useState } from "react";
import { BackLink } from "../toast";
import { ApiError, api } from "../api";
import type { ApplyRecord, ModelItem, ModelRequirements } from "../types";
import { intervalKo } from "../format";
import { SheetPicker } from "./SheetPicker";

const STATE_KO: Record<ApplyRecord["state"], string> = {
  SHEET_REQUIRED: "시트 선택 필요",
  CHECK_FAILED: "점검 실패",
  CHECKED: "점검 통과",
  RUNNING: "예측 중",
  SUCCEEDED: "예측 완료",
  FAILED: "예측 실패",
};
const TONE: Record<ApplyRecord["state"], string> = {
  SHEET_REQUIRED: "status-busy",
  CHECK_FAILED: "status-fail",
  CHECKED: "status-done",
  RUNNING: "status-busy",
  SUCCEEDED: "status-done",
  FAILED: "status-fail",
};
const MODEL_KO: Record<string, string> = { "timeseries.forecasting.xgboost": "XGBoost" };
const TZ_KO: Record<string, string> = {
  naive_as_written: "시간대 표기 없음(적힌 시각 그대로)",
  utc_converted: "시간대 표기 있음(UTC로 변환)",
};
const t = (s: string | null | undefined) => (s ? s.replace("T", " ").replace(/:00$/, "") : "—");
const n = (x: number) => x.toLocaleString("ko-KR");
const when = (iso: string | null | undefined) => (iso ? new Date(iso).toLocaleString("ko-KR") : "—");
const horizons = (hs: string[]) => hs.map((h) => `${intervalKo(h)} 뒤`).join(", ");

function modelLabel(m: ModelItem) {
  return `${m.dataset_name ?? m.dataset_id} · ${MODEL_KO[m.model ?? ""] ?? m.model} · ${horizons(m.horizons)}`;
}

export function ApplyPage({ preselect, onDataset }: { preselect: string | null; onDataset?: (id: string) => void }) {
  const appFromHash = preselect?.startsWith("app_") ? preselect : null;
  const runFromHash = preselect?.startsWith("run_") ? preselect : null;
  const [models, setModels] = useState<ModelItem[] | null>(null);
  const [runId, setRunId] = useState<string | null>(runFromHash);
  const [req, setReq] = useState<ModelRequirements | null>(null);
  const [app, setApp] = useState<ApplyRecord | null>(null);
  const [history, setHistory] = useState<ApplyRecord[]>([]);
  const [busy, setBusy] = useState<"" | "upload" | "predict" | "rebind" | "sheet">("");
  const [pickSheet, setPickSheet] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const poll = useRef<number | null>(null);

  const loadHistory = useCallback(() => {
    api
      .applications()
      .then((r) => setHistory(r.items))
      .catch(() => undefined);
  }, []);

  useEffect(() => {
    api
      .models()
      .then((r) => {
        setModels(r.items);
        setRunId((cur) => cur ?? (appFromHash ? null : r.items[0]?.run_id ?? null));
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "모델 목록을 불러오지 못했습니다"));
    loadHistory();
  }, [appFromHash, loadHistory]);

  // 적용 기록 열기(새로고침해도 복원)
  useEffect(() => {
    if (!appFromHash) {
      setApp(null);
      return;
    }
    api
      .application(appFromHash)
      .then((a) => {
        setApp(a);
        setRunId(a.run_id);
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "적용 기록을 불러오지 못했습니다"));
  }, [appFromHash]);

  useEffect(() => {
    setReq(null);
    if (!runId) return;
    api
      .model(runId)
      .then((m) => {
        setReq(m);
        onDataset?.(m.dataset_id); // 왼쪽 메뉴의 전처리·학습 설정 링크를 이 모델의 데이터셋으로
      })
      .catch((e) => setError(e instanceof ApiError ? e.message : "모델 정보를 불러오지 못했습니다"));
  }, [runId, onDataset]);

  // 예측 중이면 상태를 주기적으로 확인
  useEffect(() => {
    if (app?.state !== "RUNNING") return;
    const id = app.application_id;
    const tick = async () => {
      try {
        const a = await api.application(id);
        setApp(a);
        if (a.state === "RUNNING") poll.current = window.setTimeout(tick, 700);
        else loadHistory();
      } catch {
        poll.current = window.setTimeout(tick, 1500);
      }
    };
    poll.current = window.setTimeout(tick, 500);
    return () => {
      if (poll.current) window.clearTimeout(poll.current);
    };
  }, [app?.state, app?.application_id, loadHistory]);

  const open = (a: ApplyRecord) => {
    window.location.hash = `#/apply/${encodeURIComponent(a.application_id)}`;
  };

  const upload = async (file: File) => {
    if (!runId) return;
    setBusy("upload");
    setError(null);
    try {
      const a = await api.uploadApplication(runId, file);
      setApp(a);
      open(a);
      loadHistory();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "파일을 올리지 못했습니다");
    } finally {
      setBusy("");
      if (fileRef.current) fileRef.current.value = "";
    }
  };

  const rebind = async () => {
    if (!app || !runId) return;
    setBusy("rebind");
    setError(null);
    try {
      const a = await api.rebindApplication(app.application_id, runId);
      setApp(a);
      open(a);
      loadHistory();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "다시 점검하지 못했습니다");
    } finally {
      setBusy("");
    }
  };

  const chooseSheet = async (sheet: string) => {
    if (!app) return;
    setBusy("sheet");
    setError(null);
    try {
      const a = await api.chooseApplicationSheet(app.application_id, sheet);
      setApp(a);
      setPickSheet(false);
      if (a.application_id !== app.application_id) open(a);
      loadHistory();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "시트를 고르지 못했습니다");
    } finally {
      setBusy("");
    }
  };

  const predict = async () => {
    if (!app) return;
    setBusy("predict");
    setError(null);
    try {
      setApp(await api.predictApplication(app.application_id));
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "예측을 시작하지 못했습니다");
    } finally {
      setBusy("");
    }
  };

  if (models && models.length === 0) {
    return (
      <div className="apply">
        <h1 className="page-title">새 데이터 적용</h1>
        <div className="panel-empty">
          <h2 className="panel-title">적용할 수 있는 모델이 없습니다</h2>
          <p>학습을 완료한 실행의 모델만 적용할 수 있습니다. 학습 설정을 승인하고 학습을 시작해 완료한 뒤 다시 오세요.</p>
          <div className="actions">
            <a className="btn btn-primary" href="#/runs">
              실행·결과로
            </a>
          </div>
        </div>
      </div>
    );
  }

  const r = req?.requirements;
  const mismatch = app && runId && app.run_id !== runId;
  const chk = app?.check;
  const res = app?.result;
  const canPredict = !!app && !mismatch && (app.state === "CHECKED" || (app.state === "FAILED" && !!app.check)) && !busy;

  return (
    <div className="apply">
      <BackLink href={runId ? `#/runs/${encodeURIComponent(runId)}` : "#/runs"} label={runId ? "실행 상세" : "실행·결과"} />
      <h1 className="page-title">새 데이터 적용</h1>
      {error && (
        <div className="banner banner-fail" role="alert">
          <p>{error}</p>
        </div>
      )}

      <section className="tsec" aria-labelledby="ap-model">
        <h2 className="sub-title" id="ap-model">
          ① 모델 선택
        </h2>
        <p className="muted small">완료된 실행의 저장 모델만 표시합니다. 실패·취소된 실행은 고를 수 없습니다.</p>
        <div className="model-list" role="radiogroup" aria-labelledby="ap-model">
          {(models ?? []).map((m) => (
            <label key={m.run_id} className={`model-opt${m.run_id === runId ? " is-selected" : ""}`}>
              <input type="radio" name="apply-model" checked={m.run_id === runId} onChange={() => setRunId(m.run_id)} />
              <span>
                {m.name && <strong className="run-name">{m.name} · </strong>}
                <strong>{modelLabel(m)}</strong>
                <span className="muted small">
                  {" "}
                  · 완료 {when(m.finished_at)} · {m.run_id}
                  {m.test?.model.mae != null && ` · 테스트 MAE ${m.test.model.mae.toFixed(3)}`}
                </span>
              </span>
            </label>
          ))}
        </div>
      </section>

      {r && (
        <section className="tsec" aria-labelledby="ap-req">
          <h2 className="sub-title" id="ap-req">
            ② 필요한 입력
          </h2>
          {r.model_error ? (
            <div className="banner banner-fail" role="alert">
              <p>이 모델을 불러올 수 없습니다: {r.model_error.message_ko}</p>
            </div>
          ) : (
            <>
              <p>
                <strong>{r.target}</strong>의 {horizons(r.horizons ?? [])} 값을 예측합니다. 학습 때와 같은 열 이름의 CSV를 올려 주세요.
              </p>
              <div className="table-wrap">
                <table className="run-table req-table">
                  <thead>
                    <tr>
                      <th scope="col">열</th>
                      <th scope="col">역할</th>
                      <th scope="col">쓰는 이유</th>
                    </tr>
                  </thead>
                  <tbody>
                    {r.columns?.map((c) => (
                      <tr key={c.name}>
                        <td>
                          <code>{c.name}</code>
                        </td>
                        <td>{c.role}</td>
                        <td>{c.why}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
              <dl className="conf-summary">
                <dt>전처리</dt>
                <dd>{r.steps?.map((s) => s.name).join(" → ") || "없음"} (학습 때 저장한 설정·상태 그대로, 다시 학습하지 않음)</dd>
                <dt>과거 이력</dt>
                <dd>{r.history ?? "추가 이력 필요 없음"}</dd>
                <dt>시각 형식</dt>
                <dd>{TZ_KO[r.timezone_policy ?? ""] ?? "—"} — 새 파일도 같은 형식이어야 합니다</dd>
                <dt>다른 열</dt>
                <dd className="muted small">{r.extra_columns_policy}</dd>
              </dl>
            </>
          )}
        </section>
      )}

      <section className="tsec" aria-labelledby="ap-check">
        <h2 className="sub-title" id="ap-check">
          ③ 새 CSV 점검
        </h2>
        <div className="actions">
          <label className="btn" aria-disabled={!runId || !!r?.model_error || busy !== ""}>
            {busy === "upload" ? "올리는 중" : app ? "다른 파일 올리기" : "CSV·엑셀 파일 선택"}
            <input
              ref={fileRef}
              type="file"
              accept=".csv,.tsv,.txt,.xlsx,.xls,.xlsm"
              className="visually-hidden"
              disabled={!runId || !!r?.model_error || busy !== ""}
              onChange={(e) => e.target.files?.[0] && void upload(e.target.files[0])}
            />
          </label>
          {app && (
            <span className="muted small">
              {app.input.name} · {app.application_id} · 모델 {app.run_id}
            </span>
          )}
        </div>
        {mismatch && (
          <div className="banner banner-warn" role="status">
            <p>
              아래 점검·결과는 다른 모델({app.run_id})로 한 것입니다. 지금 고른 모델로 쓰려면 같은 파일로 다시 점검하세요.
            </p>
            <button type="button" className="btn btn-small" disabled={busy !== ""} onClick={() => void rebind()}>
              {busy === "rebind" ? "점검 중" : "이 파일로 다시 점검"}
            </button>
          </div>
        )}
        {app && (
          <div className="ap-state">
            <span className={`status ${TONE[app.state]}`}>{STATE_KO[app.state]}</span>
            {app.input.kind === "xlsx" && app.input.sheet && (
              <span className="small">
                시트 <strong>{app.input.sheet}</strong>
                {app.input.normalization?.formula_cells ? ` · 수식 ${app.input.normalization.formula_cells}칸은 저장된 결과 사용` : ""}
              </span>
            )}
            {app.input.kind === "xlsx" && app.input.sheet && !pickSheet && app.state !== "RUNNING" && !mismatch && (
              <button type="button" className="btn btn-small btn-quiet" onClick={() => setPickSheet(true)}>
                다른 시트로 점검
              </button>
            )}
            {app.error && <p className="field-error">{app.error.message_ko}</p>}
          </div>
        )}
        {app && app.input.kind === "xlsx" && app.input.sheets && (app.state === "SHEET_REQUIRED" || pickSheet) && !mismatch && (
          <SheetPicker
            sheets={app.input.sheets}
            current={app.input.sheet ?? null}
            busy={busy === "sheet"}
            action="이 시트로 점검"
            note={
              app.input.sheet
                ? "다른 시트로 점검하면 새 적용 기록을 만듭니다. 지금 기록의 점검·결과는 그대로 남습니다."
                : "학습 때와 시트 이름이 달라도, 필요한 열과 조건이 맞으면 적용할 수 있습니다."
            }
            onChoose={(s) => void chooseSheet(s)}
            onCancel={app.state === "SHEET_REQUIRED" ? undefined : () => setPickSheet(false)}
          />
        )}
        {chk && (
          <dl className="conf-summary ap-check">
            <dt>읽은 행</dt>
            <dd>
              {n(chk.rows_read)}행 → 사용 {n(chk.rows_used)}행
              {chk.rows_time_invalid > 0 && ` · 시각 해석 불가 ${n(chk.rows_time_invalid)}행 제외`}
              {chk.rows_duplicate_time_merged > 0 && ` · 같은 설비·시각 ${n(chk.rows_duplicate_time_merged)}행 평균으로 합침`}
            </dd>
            <dt>기간·설비</dt>
            <dd>
              {chk.period ? `${t(chk.period[0])} ~ ${t(chk.period[1])}` : "—"} · 설비 {chk.groups.join(", ") || "—"}
            </dd>
            <dt>예측 가능</dt>
            <dd>
              <strong>{n(chk.samples_predictable)}</strong> / {n(chk.samples_total)}개 (
              {Object.entries(chk.per_horizon)
                .map(([h, c]) => `${intervalKo(h)} 뒤 ${n(c)}`)
                .join(", ")}
              ){chk.beyond_last_observation > 0 && ` · 마지막 관측 이후 시각 ${n(chk.beyond_last_observation)}개 포함`}
            </dd>
            <dt>제외</dt>
            <dd>
              이력 부족 {n(chk.excluded.history_short)} · 기준 시각 타깃 없음 {n(chk.excluded.no_current_target)}
              {chk.excluded.missing_features > 0 && ` · 결측 피처 ${n(chk.excluded.missing_features)}`}
            </dd>
            {chk.extra_columns_ignored.length > 0 && (
              <>
                <dt>읽지 않은 열</dt>
                <dd className="muted small">{chk.extra_columns_ignored.join(", ")}</dd>
              </>
            )}
          </dl>
        )}
      </section>

      <section className="tsec" aria-labelledby="ap-run">
        <h2 className="sub-title" id="ap-run">
          ④ 예측
        </h2>
        <div className="actions">
          {app?.state !== "SUCCEEDED" && (
            <button type="button" className="btn btn-primary" disabled={!canPredict} onClick={() => void predict()}>
              {app?.state === "RUNNING" ? "예측 중" : "예측 실행"}
            </button>
          )}
          {app?.download_available && !mismatch && (
            <a className="btn" href={api.applicationCsvHref(app.application_id)} download>
              예측 결과 CSV 내려받기
            </a>
          )}
        </div>
        {app?.state === "RUNNING" && <p className="muted">저장된 모델로 예측하고 있습니다. 다른 화면을 써도 됩니다.</p>}
        {res && app?.state === "SUCCEEDED" && (
          <>
            <dl className="conf-summary">
              <dt>건수</dt>
              <dd>
                {n(res.rows)}건 ·{" "}
                {Object.entries(res.per_horizon)
                  .map(([h, c]) => `${intervalKo(h)} 뒤 ${n(c)}`)
                  .join(", ")}
              </dd>
              <dt>설비</dt>
              <dd>{res.groups.join(", ") || "—"}</dd>
              <dt>기준 시각</dt>
              <dd>
                {t(res.origin_period[0])} ~ {t(res.origin_period[1])}
              </dd>
              <dt>예측 대상</dt>
              <dd>
                {t(res.target_period[0])} ~ {t(res.target_period[1])}
                {res.beyond_last_observation > 0 && ` (입력 마지막 관측 이후 ${n(res.beyond_last_observation)}건)`}
              </dd>
              <dt>모델</dt>
              <dd className="muted small">
                {app.run_id} · <code title={app.plan_hash}>{app.plan_hash.slice(0, 19)}…</code>
              </dd>
            </dl>
            <div className="table-wrap">
              <table className="metric-table">
                <caption>결과 일부(마지막 {res.preview.length}행)</caption>
                <thead>
                  <tr>
                    <th scope="col">설비</th>
                    <th scope="col">기준 시각</th>
                    <th scope="col">예측 대상 시각</th>
                    <th scope="col">horizon</th>
                    <th scope="col" className="num">
                      예측값
                    </th>
                  </tr>
                </thead>
                <tbody>
                  {res.preview.map((row, i) => (
                    <tr key={i}>
                      <td>{String(row.group)}</td>
                      <td>{t(String(row.origin_time))}</td>
                      <td>{t(String(row.target_time))}</td>
                      <td>{intervalKo(String(row.horizon))}</td>
                      <td className="num">{Number(row.predicted).toFixed(3)}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </>
        )}
      </section>

      {history.length > 0 && (
        <section className="tsec" aria-labelledby="ap-hist">
          <h2 className="sub-title" id="ap-hist">
            최근 적용 기록
          </h2>
          <div className="table-wrap">
            <table className="run-table">
              <thead>
                <tr>
                  <th scope="col">적용</th>
                  <th scope="col">파일</th>
                  <th scope="col">모델</th>
                  <th scope="col">상태</th>
                </tr>
              </thead>
              <tbody>
                {history.slice(0, 10).map((h) => (
                  <tr key={h.application_id} className={h.application_id === app?.application_id ? "is-current" : ""}>
                    <td>
                      <a href={`#/apply/${encodeURIComponent(h.application_id)}`}>{h.application_id}</a>
                      <div className="muted small">{when(h.created_at)}</div>
                    </td>
                    <td>{h.input.name}</td>
                    <td className="small">{h.run_id}</td>
                    <td>
                      <span className={`status ${TONE[h.state]}`}>{STATE_KO[h.state]}</span>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
    </div>
  );
}
