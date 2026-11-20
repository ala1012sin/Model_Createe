import { useEffect, useState } from "react";
import { IconTable } from "./Icons";
import { api } from "../api";

interface WindowControls {
  minimize(): Promise<void>;
  toggle_maximized(): Promise<boolean>;
  close(): Promise<void>;
}

function getWindowControls() {
  return (window as Window & { pywebview?: { api: WindowControls } }).pywebview?.api;
}

export function WorkbenchBar({ query, onQueryChange, onSubmit }: {
  query: string;
  onQueryChange: (query: string) => void;
  onSubmit?: () => void; // Enter로 검색 확정
}) {
  const [controls, setControls] = useState(getWindowControls);
  const [maximized, setMaximized] = useState(false);
  // 창 닫기 확인(ADD-UX-03): null = 닫힘, 아니면 실행 중·대기 중 학습 수
  const [closing, setClosing] = useState<{ running: number; queued: number } | null>(null);
  const askClose = async () => {
    let running = 0, queued = 0;
    try {
      const r = await api.runs();
      running = r.items.filter((x) => x.state === "RUNNING" || x.state === "CANCEL_REQUESTED").length;
      queued = r.items.filter((x) => x.state === "QUEUED").length;
    } catch {
      /* 서버가 응답하지 않아도 종료 확인은 보여 준다 */
    }
    setClosing({ running, queued });
  };

  useEffect(() => {
    const sync = () => setControls(getWindowControls());
    window.addEventListener("pywebviewready", sync);
    sync();
    return () => window.removeEventListener("pywebviewready", sync);
  }, []);

  return (
    <header className="workbench-bar">
      <div className="workbench-brand pywebview-drag-region" aria-label="ML Workbench">
        <span className="workbench-mark"><IconTable /></span>
        <span className="workbench-name">ML Workbench</span>
      </div>
      <label className="workbench-search">
        <svg viewBox="0 0 20 20" aria-hidden="true"><circle cx="8.5" cy="8.5" r="5" /><path d="m12 12 4.5 4.5" /></svg>
        <input type="search" aria-label="데이터셋 이름 검색" placeholder="데이터셋 이름 검색" value={query}
          onChange={(event) => onQueryChange(event.target.value)}
          onKeyDown={(event) => {
            if (event.key === "Enter" && !event.nativeEvent.isComposing) {
              event.preventDefault();
              onSubmit?.();
            }
          }} />
      </label>
      <div className="workbench-end">
        <div className="workbench-drag pywebview-drag-region" aria-hidden="true" />
        {controls && (
          <div className="window-controls" role="group" aria-label="창 제어">
            <button type="button" aria-label="최소화" title="최소화" onClick={() => void controls.minimize()}>
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="M5 11h10" /></svg>
            </button>
            <button type="button" aria-label={maximized ? "이전 크기로" : "최대화"} title={maximized ? "이전 크기로" : "최대화"}
              onClick={async () => setMaximized(await controls.toggle_maximized())}>
              <svg viewBox="0 0 20 20" aria-hidden="true">
                {maximized ? <path d="M7 6V4h9v9h-2M4 7h9v9H4z" /> : <rect x="5" y="5" width="10" height="10" />}
              </svg>
            </button>
            <button type="button" className="window-close" aria-label="창 닫기" title="창 닫기" onClick={() => void askClose()}>
              <svg viewBox="0 0 20 20" aria-hidden="true"><path d="m5 5 10 10M15 5 5 15" /></svg>
            </button>
          </div>
        )}
      </div>
      {closing && (
        <div className="modal-back" role="presentation" onClick={() => setClosing(null)}>
          <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="close-title" onClick={(e) => e.stopPropagation()}>
            <h2 id="close-title" className="panel-title">앱을 종료합니다</h2>
            {closing.running > 0 && <p>실행 중인 학습 {closing.running}개가 중단됩니다(결과가 저장되지 않습니다).</p>}
            {closing.queued > 0 && <p>대기 중인 학습 {closing.queued}개는 다음에 앱을 켜면 이어서 시작합니다.</p>}
            {closing.running === 0 && closing.queued === 0 && <p className="muted">진행 중인 학습은 없습니다.</p>}
            <div className="actions">
              <button type="button" className="btn btn-primary" autoFocus onClick={() => void controls?.close()}>
                종료
              </button>
              <button type="button" className="btn" onClick={() => setClosing(null)}>
                취소
              </button>
            </div>
          </div>
        </div>
      )}
    </header>
  );
}
