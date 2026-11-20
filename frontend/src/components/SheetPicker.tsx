import { useId, useState } from "react";
import type { SheetInfo } from "../types";
import { nf } from "../format";

/** 엑셀 시트 선택. 시트 이름은 이 PC 화면에서만 쓰고 외부로 보내지 않는다. */
export function SheetPicker(props: {
  sheets: SheetInfo[];
  current: string | null;
  busy: boolean;
  action: string;
  note?: string;
  onChoose: (sheet: string) => void;
  onCancel?: () => void;
}) {
  const name = useId();
  const firstVisible = props.sheets.find((s) => s.state === "visible")?.name ?? props.sheets[0]?.name ?? "";
  const [pick, setPick] = useState(props.current ?? firstVisible);
  return (
    <fieldset className="sheet-picker">
      <legend>시트 선택 ({props.sheets.length}개)</legend>
      {props.note && <p className="muted small">{props.note}</p>}
      <div className="sheet-list">
        {props.sheets.map((s) => (
          <label key={s.name} className={`sheet-opt${s.name === pick ? " is-selected" : ""}`}>
            <input type="radio" name={name} checked={s.name === pick} onChange={() => setPick(s.name)} />
            <span className="sheet-name">{s.name}</span>
            <span className="muted small">
              {s.state !== "visible" ? "숨김 · " : ""}약 {nf.format(Math.max(0, s.rows_estimate - 1))}행 × {nf.format(s.cols_estimate)}열
              {s.name === props.current ? " · 현재" : ""}
            </span>
          </label>
        ))}
      </div>
      <div className="actions">
        <button type="button" className="btn btn-primary" disabled={props.busy || !pick} onClick={() => props.onChoose(pick)}>
          {props.busy ? "처리 중" : props.action}
        </button>
        {props.onCancel && (
          <button type="button" className="btn btn-quiet" onClick={props.onCancel}>
            취소
          </button>
        )}
      </div>
      <p className="muted small">크기는 시트 범위로 추정한 값이며, 실제 행·열 수는 분석 후 표시합니다.</p>
    </fieldset>
  );
}
