import { useEffect, useState } from "react";

// 시간 간격 입력: 숫자 + 단위(ADD-UX-11). 저장 값은 기존 형식(예: 60min) 그대로.
export const DURATION_PATTERN = "^\\d+(s|min|h|d)$";
export const UNITS: { v: string; ko: string }[] = [
  { v: "s", ko: "초" },
  { v: "min", ko: "분" },
  { v: "h", ko: "시간" },
  { v: "d", ko: "일" },
];
const RE = /^(\d+)(s|min|h|d)$/;

export function splitDuration(v: unknown): { n: string; unit: string } {
  const m = typeof v === "string" ? RE.exec(v) : null;
  return m ? { n: m[1], unit: m[2] } : { n: typeof v === "string" ? v.replace(/\D/g, "") : "", unit: "min" };
}

export function DurationInput({ id, value, onChange, label }: {
  id: string;
  value: unknown;
  onChange: (v: string | undefined) => void;
  label: string; // 단위 선택의 접근 가능한 이름에 쓴다
}) {
  const init = splitDuration(value);
  const [n, setN] = useState(init.n);
  const [unit, setUnit] = useState(init.unit);
  useEffect(() => {
    const s = splitDuration(value);
    if (`${s.n}${s.unit}` !== `${n}${unit}` && value !== undefined) {
      setN(s.n);
      setUnit(s.unit);
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [value]);
  const emit = (nn: string, uu: string) => onChange(nn === "" ? undefined : `${nn}${uu}`);
  return (
    <span className="duration-input">
      <input
        id={id}
        type="number"
        min={1}
        step={1}
        inputMode="numeric"
        value={n}
        onChange={(e) => {
          const nn = e.target.value.replace(/[^\d]/g, "");
          setN(nn);
          emit(nn, unit);
        }}
      />
      <select aria-label={`${label} 단위`} value={unit} onChange={(e) => {
        setUnit(e.target.value);
        emit(n, e.target.value);
      }}>
        {UNITS.map((u) => (
          <option key={u.v} value={u.v}>
            {u.ko}
          </option>
        ))}
      </select>
    </span>
  );
}
