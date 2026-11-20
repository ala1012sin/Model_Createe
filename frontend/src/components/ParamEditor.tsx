import { useId, useState } from "react";
import type { ColumnDtype, JsonSchema, ValidationIssue } from "../types";
import { DTYPE_KO } from "../format";
import { DURATION_PATTERN, DurationInput } from "./DurationInput";

// 플러그인 parameter_schema(JSON Schema 부분집합)로 입력란을 만든다. 형식 검사는 서버 검증 결과를 그대로 보여준다.

type Params = Record<string, unknown>;

const PARAM_KO: Record<string, string> = {
  gap: "세션을 나누는 간격",
  frequency: "새 간격",
  aggregation: "집계 방법",
  method: "방법",
  max_gap: "최대 채움 간격",
  n_estimators: "트리 개수",
  max_depth: "최대 깊이",
  learning_rate: "학습률",
  lags: "몇 칸 전 값을 쓸지",
  windows: "구간 길이(칸)",
  stats: "통계",
  shift_first: "현재 시점 제외",
  columns: "대상 열",
  periods: "몇 칸 차이",
  features: "추가할 특징",
};

const ENUM_KO: Record<string, string> = {
  mean: "평균", median: "중앙값", last: "마지막 값", sum: "합계", max: "최댓값", min: "최솟값", std: "표준편차",
  linear: "선형 보간", ffill: "직전 값으로 채움", drop: "행 제외", standard: "표준화", minmax: "최소·최대",
  robust: "로버스트", hour: "시", dayofweek: "요일", month: "월", is_weekend: "주말 여부",
};

function ArrayText({ id, value, onChange, numeric }: { id: string; value: unknown; numeric: boolean;
  onChange: (v: unknown[] | undefined) => void }) {
  const initial = Array.isArray(value) ? value.join(", ") : "";
  const [text, setText] = useState(initial);
  return (
    <input
      id={id}
      value={text}
      inputMode={numeric ? "numeric" : "text"}
      placeholder={numeric ? "예: 1, 2, 4" : ""}
      onChange={(e) => {
        setText(e.target.value);
        const parts = e.target.value.split(/[,\s]+/).filter(Boolean);
        // 숫자로 읽히지 않는 값은 문자열 그대로 보내 서버가 위치와 함께 알려 주게 한다
        onChange(parts.length ? parts.map((p) => (numeric && /^-?\d+(\.\d+)?$/.test(p) ? Number(p) : p)) : undefined);
      }}
    />
  );
}

export function ParamEditor(props: {
  schema: JsonSchema;
  params: Params;
  columns: { name: string; dtype: ColumnDtype }[];
  issues: ValidationIssue[];
  pathPrefix: string; // preprocessing[2].params
  onChange: (next: Params) => void;
}) {
  const base = useId();
  const props_ = props.schema.properties ?? {};
  const required = new Set(props.schema.required ?? []);
  const keys = Object.keys(props_);
  if (!keys.length) return <p className="muted">편집할 파라미터가 없습니다.</p>;

  const set = (key: string, v: unknown) => {
    const next = { ...props.params };
    if (v === undefined || v === "") delete next[key];
    else next[key] = v;
    props.onChange(next);
  };

  return (
    <div className="params">
      {keys.map((key) => {
        const s = props_[key];
        const id = `${base}-${key}`;
        const v = props.params[key];
        const msgs = props.issues.filter(
          (i) => i.path === `${props.pathPrefix}.${key}` || i.path.startsWith(`${props.pathPrefix}.${key}[`),
        );
        const label = (
          <label htmlFor={id}>
            {PARAM_KO[key] ?? s.title ?? key}
            <span className="param-key"> {key}</span>
            {required.has(key) && <span className="req"> (필수)</span>}
          </label>
        );
        let control: JSX.Element;
        if (s.type === "boolean" && s.const !== undefined) {
          control = (
            <p className="fixed-param" id={id}>
              {s.const ? "항상 켜짐 — 미래 값이 섞이지 않도록 고정" : "항상 꺼짐"}
            </p>
          );
        } else if (s.type === "boolean") {
          control = <input id={id} type="checkbox" checked={Boolean(v)} onChange={(e) => set(key, e.target.checked)} />;
        } else if (s.type === "integer" || s.type === "number") {
          const hasDefault = s.default !== undefined && s.default !== null;
          control = (
            <span className="num-default">
              <input
                id={id}
                type="number"
                step={s.type === "integer" ? 1 : "any"}
                min={s.minimum}
                max={s.maximum}
                value={typeof v === "number" ? v : ""}
                placeholder={hasDefault ? `기본값 ${String(s.default)}` : ""}
                onChange={(e) => set(key, e.target.value === "" ? undefined : Number(e.target.value))}
              />
              {hasDefault &&
                (typeof v === "number" ? (
                  v !== s.default && (
                    <button type="button" className="btn btn-small btn-quiet" onClick={() => set(key, undefined)}>
                      기본값({String(s.default)})으로
                    </button>
                  )
                ) : (
                  <span className="muted small">비워 두면 기본값 {String(s.default)}</span>
                ))}
            </span>
          );
        } else if (s.type === "string" && s.pattern === DURATION_PATTERN) {
          control = <DurationInput id={id} value={v} label={PARAM_KO[key] ?? s.title ?? key} onChange={(d) => set(key, d)} />;
        } else if (s.type === "string" && s.enum) {
          control = (
            <select id={id} value={typeof v === "string" ? v : ""} onChange={(e) => set(key, e.target.value)}>
              <option value="">선택하세요</option>
              {typeof v === "string" && v && !s.enum.map(String).includes(v) && (
                // 이전 초안에 남은, 더 이상 제공하지 않는 값: 다른 값으로 바꿔 보이지 않게 그대로 표시
                <option value={v} disabled>
                  {ENUM_KO[v] ?? v} (사용할 수 없음)
                </option>
              )}
              {s.enum.map((o) => (
                <option key={String(o)} value={String(o)}>
                  {ENUM_KO[String(o)] ?? String(o)}
                </option>
              ))}
            </select>
          );
        } else if (s.type === "array" && (s["x-column"] || s.items?.enum)) {
          const options = s["x-column"]
            ? props.columns.map((c) => ({ value: c.name, text: `${c.name} (${DTYPE_KO[c.dtype]})` }))
            : (s.items?.enum ?? []).map((o) => ({ value: String(o), text: ENUM_KO[String(o)] ?? String(o) }));
          const cur = Array.isArray(v) ? (v as string[]) : [];
          control = (
            <fieldset className="checks" id={id} aria-label={PARAM_KO[key] ?? s.title ?? key}>
              {options.map((o) => (
                <label key={o.value} className="check">
                  <input
                    type="checkbox"
                    checked={cur.includes(o.value)}
                    onChange={(e) => {
                      const next = e.target.checked ? [...cur, o.value] : cur.filter((x) => x !== o.value);
                      set(key, next.length ? next : undefined);
                    }}
                  />
                  {o.text}
                </label>
              ))}
            </fieldset>
          );
        } else if (s.type === "array") {
          control = (
            <ArrayText key={`${props.pathPrefix}-${key}`} id={id} value={v} numeric={s.items?.type === "integer" || s.items?.type === "number"}
              onChange={(arr) => set(key, arr)} />
          );
        } else {
          control = (
            <input
              id={id}
              value={typeof v === "string" ? v : ""}
              placeholder={s.examples?.[0] ? `예: ${s.examples[0]}` : ""}
              onChange={(e) => set(key, e.target.value)}
            />
          );
        }
        return (
          <div className={`field${msgs.some((m) => m.severity === "error") ? " has-error" : ""}`} key={key}>
            {label}
            {control}
            {msgs.map((m, n) => (
              <p key={n} className={m.severity === "error" ? "field-error" : "field-warn"}>
                {m.message_ko}
              </p>
            ))}
          </div>
        );
      })}
    </div>
  );
}
