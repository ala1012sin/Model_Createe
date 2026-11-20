import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api";
import type { CompareRun, CompareSeries, CompareView, MetricBlock } from "../types";
import { intervalKo } from "../format";

// S6 실험 비교 패널 (docs/P2_COMPARE_BRIEF.md). 평가 조건이 다른 실행은 경고하고 최고값 표시에서 뺀다.
// 실행 색은 선택 순서대로 고정(검증한 범주 팔레트 6색, 라이트 배경). 실제값은 본문 글자색.
export const SERIES_COLORS = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"];
const METRIC_KO: Record<string, string> = { mae: "MAE", rmse: "RMSE", r2: "R²", smape: "sMAPE(%)" };
const HIGHER_BETTER = new Set(["r2"]);
// 비교 표는 근소한 차이가 보이도록 유효숫자 4자리
const fmt = (v: number | null | undefined) =>
  v === null || v === undefined ? "—" : Math.abs(v) >= 1000 ? v.toFixed(1) : Number(v.toPrecision(4)).toString();
const durKo = (s: number | null) => (s == null ? "—" : s < 60 ? `${s}초` : `${Math.floor(s / 60)}분 ${s % 60}초`);
const short = (id: string) => id.replace(/^run_/, "").slice(0, 6);
const label = (r: { run_id: string; name?: string | null }) => r.name || short(r.run_id);

function valueText(v: unknown): string {
  if (v === null || v === undefined) return "—";
  if (typeof v === "string") return v;
  if (Array.isArray(v)) return v.map(String).join(", ");
  if (typeof v === "object")
    return Object.entries(v as Record<string, unknown>)
      .filter(([, x]) => x !== null && x !== undefined)
      .map(([k, x]) => `${k} ${String(x)}`)
      .join(" · ");
  return String(v);
}

function Swatch({ i }: { i: number }) {
  return <span className="swatch" style={{ background: SERIES_COLORS[i % SERIES_COLORS.length] }} aria-hidden="true" />;
}

export function ComparePanel({ runIds }: { runIds: string[] }) {
  const [view, setView] = useState<CompareView | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [metric, setMetric] = useState("mae");
  const [byGroup, setByGroup] = useState(false);
  const key = runIds.join(",");

  useEffect(() => {
    let alive = true;
    setError(null);
    api
      .compare(runIds)
      .then((v) => {
        if (!alive) return;
        setView(v);
        if (!v.metric_names.includes(metric)) setMetric(v.metric_names[0] ?? "mae");
      })
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "비교 결과를 불러오지 못했습니다"));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (error) return <p className="field-error">{error}</p>;
  if (!view || view.runs.map((r) => r.run_id).join(",") !== key) return <p className="muted">비교 결과를 불러오는 중…</p>;
  const ref = view.runs[0];
  const flagged = view.runs.filter((r) => !r.same_protocol);

  return (
    <section className="tsec compare" aria-labelledby="cmp-title">
      <h2 id="cmp-title" className="tsec-title">
        실험 비교 · {view.runs.length}개
      </h2>
      <p className="muted">
        기준 실행: <strong>{ref.run_id}</strong>(처음 고른 실행). 평가 조건(데이터·분할·테스트 표본·지표)이 기준과 같은 실행끼리만
        최고값을 표시합니다.
      </p>
      <ul className="cmp-legend" aria-label="실행 색">
        {view.runs.map((r, i) => (
          <li key={r.run_id}>
            <Swatch i={i} /> {r.name ? <strong>{r.name}</strong> : null} {r.run_id}
            <span className="muted"> · {r.steps.join(" → ") || "전처리 없음"} · 소요 {durKo(r.duration_s)}</span>{" "}
            <a href={`#/runs/${encodeURIComponent(r.run_id)}`}>상세</a> ·{" "}
            <a href={`#/apply/${encodeURIComponent(r.run_id)}`}>새 데이터 적용 →</a>
          </li>
        ))}
      </ul>

      {flagged.length > 0 && (
        <div className="banner banner-warn" role="status">
          <p>
            <strong>⚠ 평가 조건이 기준과 다른 실행이 있습니다.</strong> 테스트 표본이나 기준이 달라 지표를 직접 비교할 수 없습니다.
          </p>
          <ul>
            {flagged.map((r) => (
              <li key={r.run_id}>
                {r.run_id}:{" "}
                {r.protocol_diffs
                  .map((d) => (d.key === "test_set" ? `${d.label_ko}이 다름` : `${d.label_ko}(${valueText(d.reference)} → ${valueText(d.value)})`))
                  .join(", ")}
                {r.comparable_horizons.length > 0 && ` — 같은 표본인 예측 시점: ${r.comparable_horizons.map(intervalKo).join(", ")}`}
              </li>
            ))}
          </ul>
        </div>
      )}

      <h3 className="sub-title">조건 차이</h3>
      {view.condition_diffs.length === 0 ? (
        <p className="muted">학습 조건(전처리·모델·파라미터·학습량)이 모두 같습니다.</p>
      ) : (
        <div className="table-wrap">
          <table className="metric-table cmp-diff">
            <thead>
              <tr>
                <th scope="col">항목</th>
                {view.runs.map((r, i) => (
                  <th scope="col" key={r.run_id}>
                    <Swatch i={i} /> {label(r)}
                  </th>
                ))}
              </tr>
            </thead>
            <tbody>
              {view.condition_diffs.map((d) => (
                <tr key={d.key}>
                  <th scope="row">{d.label_ko}</th>
                  {d.values.map((v, i) => (
                    <td key={i} className={i > 0 && v !== d.values[0] ? "cmp-changed" : undefined}>
                      {v}
                    </td>
                  ))}
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <MetricCompare view={view} metric={metric} setMetric={setMetric} byGroup={byGroup} setByGroup={setByGroup} />
      <SeriesChart view={view} />
    </section>
  );
}

function MetricCompare(props: {
  view: CompareView;
  metric: string;
  setMetric: (m: string) => void;
  byGroup: boolean;
  setByGroup: (b: boolean) => void;
}) {
  const { view, metric } = props;
  const better = (a: number, b: number) => (HIGHER_BETTER.has(metric) ? a > b : a < b);
  type Row = { label: string; get: (r: CompareRun) => MetricBlock | undefined; ok: (r: CompareRun) => boolean };
  const rows: Row[] = [
    { label: "전체", get: (r) => r.metrics.test?.overall, ok: (r) => r.same_protocol },
    ...view.horizons.map((h) => ({
      label: `${intervalKo(h)} 뒤`,
      get: (r: CompareRun) => r.metrics.test?.per_horizon?.[h],
      ok: (r: CompareRun) => r.comparable_horizons.includes(h),
    })),
    ...(props.byGroup
      ? view.groups.map((g) => ({
          label: `설비 ${g || "(없음)"}`,
          get: (r: CompareRun) => r.metrics.test?.per_group?.[g || "__all__"] ?? r.metrics.test?.per_group?.[g],
          ok: (r: CompareRun) => r.same_protocol,
        }))
      : []),
  ];
  return (
    <>
      <h3 className="sub-title">테스트 지표</h3>
      <div className="cmp-controls">
        <label>
          지표{" "}
          <select aria-label="비교 지표" value={metric} onChange={(e) => props.setMetric(e.target.value)}>
            {view.metric_names.map((m) => (
              <option key={m} value={m}>
                {METRIC_KO[m] ?? m}
              </option>
            ))}
          </select>
        </label>
        <label className="check">
          <input type="checkbox" checked={props.byGroup} onChange={(e) => props.setByGroup(e.target.checked)} /> 설비별도 보기
        </label>
        <span className="muted small">
          {HIGHER_BETTER.has(metric) ? "클수록 좋음" : "작을수록 좋음"} · 굵게 = 같은 평가 조건 중 최고 · 괄호 = 직전값 기준 예측
        </span>
      </div>
      <div className="table-wrap">
        <table className="metric-table cmp-metrics">
          <thead>
            <tr>
              <th scope="col">구분</th>
              {view.runs.map((r, i) => (
                <th scope="col" className="num" key={r.run_id}>
                  <Swatch i={i} /> {label(r)}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row) => {
              const vals = view.runs.map((r) => row.get(r)?.model?.[metric as keyof MetricBlock["model"]] ?? null);
              let best: number | null = null;
              view.runs.forEach((r, i) => {
                const v = vals[i];
                if (v != null && row.ok(r) && (best === null || better(v, best))) best = v;
              });
              const comparableCount = view.runs.filter((r) => row.ok(r)).length;
              return (
                <tr key={row.label}>
                  <th scope="row">{row.label}</th>
                  {view.runs.map((r, i) => {
                    const b = row.get(r);
                    const v = vals[i];
                    const base = b?.baseline?.[metric as keyof MetricBlock["model"]];
                    const isBest = v != null && v === best && comparableCount > 1 && row.ok(r);
                    return (
                      <td key={r.run_id} className={`num${row.ok(r) ? "" : " cmp-na"}`}>
                        {!row.ok(r) && v != null && <span title="평가 조건이 기준과 달라 직접 비교할 수 없습니다">⚠ </span>}
                        {isBest ? <strong>{fmt(v)}</strong> : fmt(v)}
                        {base != null && <span className="muted"> ({fmt(base)})</span>}
                        {b && <div className="muted small">n={b.n}</div>}
                      </td>
                    );
                  })}
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
    </>
  );
}

function SeriesChart({ view }: { view: CompareView }) {
  const [horizon, setHorizon] = useState(view.horizons[0] ?? "");
  const [group, setGroup] = useState(view.groups[0] ?? "");
  const [data, setData] = useState<CompareSeries | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [hover, setHover] = useState<number | null>(null);
  const ids = view.runs.map((r) => r.run_id);
  const idKey = ids.join(",");

  useEffect(() => {
    let alive = true;
    setError(null);
    setHover(null);
    api
      .compareSeries(ids, horizon, group)
      .then((d) => alive && setData(d))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "그래프 자료를 불러오지 못했습니다"));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [idKey, horizon, group]);

  const W = 960, H = 280, L = 56, R = 12, T = 12, B = 34;
  const geo = useMemo(() => {
    if (!data || data.points_shown === 0) return null;
    const all = [...data.actual, ...data.predicted.flat()].filter((v) => Number.isFinite(v));
    let lo = Math.min(...all), hi = Math.max(...all);
    if (hi === lo) { hi += 1; lo -= 1; }
    const pad = (hi - lo) * 0.06;
    lo -= pad; hi += pad;
    const n = data.points_shown;
    const x = (i: number) => L + (n === 1 ? 0 : (i / (n - 1)) * (W - L - R));
    const y = (v: number) => T + (1 - (v - lo) / (hi - lo)) * (H - T - B);
    const path = (vals: number[]) => vals.map((v, i) => `${i ? "L" : "M"}${x(i).toFixed(1)},${y(v).toFixed(1)}`).join("");
    const ticks = Array.from({ length: 5 }, (_, k) => lo + ((hi - lo) * k) / 4);
    return { x, y, path, ticks, n };
  }, [data]);

  return (
    <>
      <h3 className="sub-title">실제 vs 예측 (테스트 구간)</h3>
      <div className="cmp-controls">
        <label>
          예측 시점{" "}
          <select aria-label="그래프 예측 시점" value={horizon} onChange={(e) => setHorizon(e.target.value)}>
            {view.horizons.map((h) => (
              <option key={h} value={h}>
                {intervalKo(h)} 뒤
              </option>
            ))}
          </select>
        </label>
        {view.groups.length > 1 && (
          <label>
            설비{" "}
            <select aria-label="그래프 설비" value={group} onChange={(e) => setGroup(e.target.value)}>
              {view.groups.map((g) => (
                <option key={g} value={g}>
                  {g}
                </option>
              ))}
            </select>
          </label>
        )}
        {data && (
          <span className="muted small">
            선택한 실행 모두에 있는 표본만 · {data.points_shown.toLocaleString()}점
            {data.points_total > data.points_shown && ` (전체 ${data.points_total.toLocaleString()}점 중 고르게 추림)`}
          </span>
        )}
      </div>
      {error && <p className="field-error">{error}</p>}
      {data && !geo && <p className="muted">이 예측 시점·설비에 모든 실행이 함께 가진 테스트 표본이 없습니다.</p>}
      {data && geo && (
        <div className="cmp-chart">
          <ul className="cmp-legend inline" aria-hidden="true">
            <li>
              <span className="swatch swatch-actual" /> 실제값
            </li>
            {view.runs.map((r, i) => (
              <li key={r.run_id}>
                <Swatch i={i} /> {label(r)}
              </li>
            ))}
          </ul>
          <svg
            viewBox={`0 0 ${W} ${H}`}
            role="img"
            aria-label={`${intervalKo(horizon)} 뒤 예측, 실제값과 실행 ${view.runs.length}개의 예측값 그래프`}
            onMouseLeave={() => setHover(null)}
            onMouseMove={(e) => {
              const box = (e.currentTarget as SVGSVGElement).getBoundingClientRect();
              const px = ((e.clientX - box.left) / box.width) * W;
              const i = Math.round(((px - L) / (W - L - R)) * (geo.n - 1));
              setHover(Math.max(0, Math.min(geo.n - 1, i)));
            }}
          >
            {geo.ticks.map((t) => (
              <g key={t}>
                <line x1={L} x2={W - R} y1={geo.y(t)} y2={geo.y(t)} className="grid" />
                <text x={L - 6} y={geo.y(t) + 4} textAnchor="end" className="tick">
                  {fmt(t)}
                </text>
              </g>
            ))}
            <text x={L} y={H - 10} className="tick">
              {data.target_time[0].replace("T", " ").slice(0, 16)}
            </text>
            <text x={W - R} y={H - 10} textAnchor="end" className="tick">
              {data.target_time[geo.n - 1].replace("T", " ").slice(0, 16)}
            </text>
            <path d={geo.path(data.actual)} className="line-actual" />
            {data.predicted.map((p, i) => (
              <path key={i} d={geo.path(p)} className="line-pred" stroke={SERIES_COLORS[i % SERIES_COLORS.length]} />
            ))}
            {hover !== null && (
              <g>
                <line x1={geo.x(hover)} x2={geo.x(hover)} y1={T} y2={H - B} className="crosshair" />
                <circle cx={geo.x(hover)} cy={geo.y(data.actual[hover])} r={4} className="dot-actual" />
                {data.predicted.map((p, i) => (
                  <circle key={i} cx={geo.x(hover)} cy={geo.y(p[hover])} r={4} fill={SERIES_COLORS[i % SERIES_COLORS.length]} className="dot" />
                ))}
              </g>
            )}
          </svg>
          {hover !== null && (
            <div className="cmp-tip" role="status">
              <strong>{data.target_time[hover].replace("T", " ").slice(0, 16)}</strong> · 실제 {fmt(data.actual[hover])}
              {data.predicted.map((p, i) => (
                <span key={i}>
                  {" "}
                  · <Swatch i={i} /> {label(view.runs[i])} {fmt(p[hover])}
                </span>
              ))}
            </div>
          )}
        </div>
      )}
    </>
  );
}
