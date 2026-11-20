import { useEffect, useId, useState } from "react";
import { ApiError, api } from "../api";
import type { Goal, LocalProfile, ValidationIssue } from "../types";
import { DTYPE_KO, intervalKo } from "../format";
import { toast } from "../toast";
import { UNITS } from "./DurationInput";

const PRESETS = ["15min", "30min", "1h"];
const HORIZON = /^\d+(s|min|h|d)$/;

export function GoalForm({
  datasetId,
  profile,
  onSaved,
  preprocessHref,
}: {
  datasetId: string;
  profile: LocalProfile;
  onSaved: () => void;
  preprocessHref: string | null;
}) {
  const firstTime = profile.time_candidates[0]?.name ?? "";
  const [goal, setGoal] = useState<Goal>({ timestamp_col: firstTime, target_col: "", group_col: "", horizons: [] });
  const [draftHorizon, setDraftHorizon] = useState("");
  const [draftUnit, setDraftUnit] = useState("min");
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [status, setStatus] = useState<{ kind: "idle" | "saving" | "saved" | "error"; text?: string }>({ kind: "idle" });
  const ids = { ts: useId(), target: useId(), group: useId(), hz: useId() };

  useEffect(() => {
    let alive = true;
    setIssues([]);
    setStatus({ kind: "idle" });
    api
      .getGoal(datasetId)
      .then((g) => {
        if (!alive) return;
        if (g.saved && g.goal) {
          setGoal({ ...g.goal, group_col: g.goal.group_col ?? "" });
          setStatus({ kind: "saved", text: "저장된 목표를 불러왔습니다" });
        } else {
          setGoal({ timestamp_col: firstTime, target_col: "", group_col: "", horizons: [] });
        }
      })
      .catch(() => undefined);
    return () => {
      alive = false;
    };
  }, [datasetId, profile.profile_version, firstTime]);

  const cols = profile.columns;
  const timeCols = [...cols].sort((a, b) => Number(b.dtype === "datetime") - Number(a.dtype === "datetime"));
  const numeric = cols.filter((c) => c.dtype === "numeric");
  const groupable = cols.filter((c) => c.dtype === "categorical" || c.dtype === "text");
  const msgFor = (field: string) => issues.filter((i) => i.path.startsWith(`user_goal.${field}`));

  const addHorizon = (h: string) => {
    const v = h.trim();
    if (!v) return;
    if (!HORIZON.test(v)) {
      setIssues([{ code: "LOCAL", severity: "error", path: "user_goal.horizons", message_ko: "예측 구간은 1 이상의 숫자와 단위로 입력하세요" }]);
      return;
    }
    setGoal((g) => (g.horizons.includes(v) ? g : { ...g, horizons: [...g.horizons, v] }));
    setDraftHorizon("");
    setStatus({ kind: "idle" });
  };

  const change = (patch: Partial<Goal>) => {
    setGoal((g) => ({ ...g, ...patch }));
    setStatus({ kind: "idle" });
  };

  const save = async (e: React.FormEvent) => {
    e.preventDefault();
    setStatus({ kind: "saving" });
    try {
      const out = await api.saveGoal(datasetId, {
        ...goal,
        timestamp_col: goal.timestamp_col || null,
        target_col: goal.target_col || null,
        group_col: goal.group_col || null,
      });
      setIssues(out.issues);
      setStatus({ kind: "saved", text: "목표를 저장했습니다" });
      onSaved();
      // 오류 없이 저장되면 알림 후 전처리로 바로 이동(ADD-UX-09). 오류가 남아 있으면 이 자리에서 고치게 둔다.
      if (!out.issues.some((i) => i.severity === "error")) {
        toast("목표를 저장했습니다 — 전처리로 이동합니다");
        window.location.hash = `#/preprocess/${encodeURIComponent(datasetId)}`;
      }
    } catch (err) {
      if (err instanceof ApiError) {
        setIssues(err.body.issues ?? []);
        setStatus({ kind: "error", text: err.message });
      } else setStatus({ kind: "error", text: "저장하지 못했습니다" });
    }
  };

  const FieldMsgs = ({ field }: { field: string }) => (
    <>
      {msgFor(field).map((i, n) => (
        <p key={n} className={i.severity === "error" ? "field-error" : "field-warn"}>
          {i.message_ko}
        </p>
      ))}
    </>
  );

  return (
    <form className="goal" onSubmit={save} aria-labelledby="goal-title" noValidate>
      <h3 className="sub-title" id="goal-title">
        예측 목표
      </h3>
      <p className="muted">학습에 쓸 시간열과 타깃을 정합니다. 저장하면 전처리 화면으로 넘어갈 수 있습니다.</p>
      <div className="goal-grid">
        <div className="field">
          <label htmlFor={ids.ts}>시간열</label>
          <select id={ids.ts} value={goal.timestamp_col ?? ""} onChange={(e) => change({ timestamp_col: e.target.value })}>
            <option value="">선택하세요</option>
            {timeCols.map((c) => (
              <option key={c.alias} value={c.name}>
                {c.name} ({DTYPE_KO[c.dtype]})
              </option>
            ))}
          </select>
          <FieldMsgs field="timestamp_col" />
        </div>
        <div className="field">
          <label htmlFor={ids.target}>예측 타깃</label>
          <select id={ids.target} value={goal.target_col ?? ""} onChange={(e) => change({ target_col: e.target.value })}>
            <option value="">선택하세요</option>
            {numeric.map((c) => (
              <option key={c.alias} value={c.name}>
                {c.name}
              </option>
            ))}
          </select>
          {numeric.length === 0 && <p className="field-warn">수치형 열이 없어 타깃을 고를 수 없습니다.</p>}
          <FieldMsgs field="target_col" />
        </div>
        <div className="field">
          <label htmlFor={ids.group}>설비 구분 열 (선택)</label>
          <select id={ids.group} value={goal.group_col ?? ""} onChange={(e) => change({ group_col: e.target.value })}>
            <option value="">사용 안 함</option>
            {groupable.map((c) => (
              <option key={c.alias} value={c.name}>
                {c.name} (고유값 {c.distinct_count})
              </option>
            ))}
          </select>
          <FieldMsgs field="group_col" />
        </div>
        <div className="field">
          <label htmlFor={ids.hz}>예측 구간</label>
          <div className="chips" aria-live="polite">
            {goal.horizons.map((h) => (
              <span key={h} className="chip">
                {intervalKo(h)} 뒤
                <button
                  type="button"
                  className="chip-x"
                  aria-label={`${intervalKo(h)} 뒤 예측 구간 삭제`}
                  onClick={() => change({ horizons: goal.horizons.filter((x) => x !== h) })}
                >
                  ×
                </button>
              </span>
            ))}
          </div>
          <div className="hz-row">
            <input
              id={ids.hz}
              type="number"
              min={1}
              step={1}
              inputMode="numeric"
              value={draftHorizon}
              placeholder="예: 15"
              onChange={(e) => setDraftHorizon(e.target.value.replace(/[^\d]/g, ""))}
              onKeyDown={(e) => {
                if (e.key === "Enter") {
                  e.preventDefault();
                  if (draftHorizon) addHorizon(`${draftHorizon}${draftUnit}`);
                }
              }}
            />
            <select aria-label="예측 구간 단위" value={draftUnit} onChange={(e) => setDraftUnit(e.target.value)}>
              {UNITS.map((u) => (
                <option key={u.v} value={u.v}>
                  {u.ko}
                </option>
              ))}
            </select>
            <button type="button" className="btn btn-small" disabled={!draftHorizon} onClick={() => addHorizon(`${draftHorizon}${draftUnit}`)}>
              추가
            </button>
            {PRESETS.filter((p) => !goal.horizons.includes(p)).map((p) => (
              <button key={p} type="button" className="btn btn-small btn-quiet" onClick={() => addHorizon(p)}>
                {intervalKo(p)}
              </button>
            ))}
          </div>
          <FieldMsgs field="horizons" />
        </div>
      </div>
      {issues
        .filter((i) => !i.path.startsWith("user_goal."))
        .map((i, n) => (
          <p key={n} className={i.severity === "error" ? "field-error" : "field-warn"}>
            {i.message_ko}
          </p>
        ))}
      <div className="goal-actions">
        <button type="submit" className="btn btn-primary" disabled={status.kind === "saving"}>
          {status.kind === "saving" ? "저장 중" : "목표 저장"}
        </button>
        {status.text && (
          <p className={status.kind === "error" ? "field-error" : "saved-text"} role="status">
            {status.text}
          </p>
        )}
        {preprocessHref && status.kind === "saved" && (
          <a className="btn btn-quiet" href={preprocessHref}>
            전처리로 이동
          </a>
        )}
      </div>
    </form>
  );
}
