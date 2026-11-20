import { useRef } from "react";
import type { DatasetState } from "../types";
import { STATE_KO, elapsed, nf, toneOf } from "../format";

export interface ListEntry {
  id: string;
  name: string;
  state: DatasetState | "LOCAL_UPLOADING";
  ratio?: number | null;
  rows?: number | null;
  cols?: number | null;
  errorText: string | null;
  since?: string | null;
  local: boolean;
  needsSheet?: boolean;
  images?: number | null; // 이미지 데이터셋(비전)이면 장수
  sheet?: string | null;
}

function StatusText({ e, serverNow }: { e: ListEntry; serverNow: number }) {
  const label = STATE_KO[e.state];
  if (e.needsSheet) return <>시트 선택 필요</>;
  if (e.state === "LOCAL_UPLOADING" || e.state === "UPLOADING") {
    return <>{e.ratio != null ? `${label} ${Math.round(e.ratio * 100)}%` : label}</>;
  }
  if (e.state === "PROFILING" || e.state === "REGISTERED") {
    return <>{`${label} ${elapsed(e.since ?? null, serverNow)}`}</>;
  }
  return <>{label}</>;
}

export function DatasetList(props: {
  entries: ListEntry[];
  filtering: boolean;
  selected: string | null;
  serverNow: number;
  loaded: boolean;
  loadError: string | null;
  onSelect: (id: string) => void;
  onRetryFile: (id: string) => void;
  onReload: () => void;
}) {
  const refs = useRef<Map<string, HTMLButtonElement>>(new Map());
  const { entries, selected } = props;
  const selectionVisible = entries.some((entry) => entry.id === selected);

  const move = (from: number, to: number) => {
    if (to < 0 || to >= entries.length || to === from) return;
    const id = entries[to].id;
    props.onSelect(id);
    refs.current.get(id)?.focus();
  };

  return (
    <div className="list-block">
      <h2 className="section-title" id="list-title">
        등록 목록 <span className="count">{entries.length ? nf.format(entries.length) : ""}</span>
      </h2>
      {props.loadError && (
        <div className="inline-error" role="alert">
          <p>{props.loadError}</p>
          <button type="button" className="btn btn-quiet" onClick={props.onReload}>
            다시 불러오기
          </button>
        </div>
      )}
      {props.loaded && !props.loadError && entries.length === 0 && (
        <p className="empty-text" role="status">{props.filtering ? "검색 결과가 없습니다." : "등록된 데이터셋이 없습니다."}</p>
      )}
      <ul className="ds-list" aria-labelledby="list-title">
        {entries.map((e, i) => {
          const tone = toneOf(e.state);
          const isSel = e.id === selected;
          return (
            <li key={e.id} className={`ds-row${isSel ? " is-selected" : ""}`}>
              <button
                type="button"
                className="ds-select"
                ref={(el) => {
                  if (el) refs.current.set(e.id, el);
                  else refs.current.delete(e.id);
                }}
                aria-current={isSel ? "true" : undefined}
                tabIndex={isSel || (!selectionVisible && i === 0) ? 0 : -1}
                onClick={() => props.onSelect(e.id)}
                onKeyDown={(ev) => {
                  if (ev.key === "ArrowDown") (ev.preventDefault(), move(i, i + 1));
                  else if (ev.key === "ArrowUp") (ev.preventDefault(), move(i, i - 1));
                  else if (ev.key === "Home") (ev.preventDefault(), move(i, 0));
                  else if (ev.key === "End") (ev.preventDefault(), move(i, entries.length - 1));
                }}
              >
                <span className="ds-name">
                  {e.name}
                  {e.sheet && <span className="ds-sheet"> · {e.sheet}</span>}
                </span>
                <span className="ds-meta">
                  <span className={`status status-${tone}`}>
                    <StatusText e={e} serverNow={props.serverNow} />
                  </span>
                  {e.state === "PROFILED" && e.images != null && <span>이미지 {nf.format(e.images)}장</span>}
                  {e.state === "PROFILED" && e.rows != null && (
                    <span className="ds-shape">
                      {nf.format(e.rows)}행 {e.cols}열
                    </span>
                  )}
                </span>
                {e.errorText && <span className="ds-error">{e.errorText}</span>}
              </button>
              {tone === "fail" && !e.local && (
                <button type="button" className="btn btn-small" onClick={() => props.onRetryFile(e.id)}>
                  수정한 파일로 다시 등록
                  <span className="visually-hidden">: {e.name}</span>
                </button>
              )}
            </li>
          );
        })}
      </ul>
    </div>
  );
}
