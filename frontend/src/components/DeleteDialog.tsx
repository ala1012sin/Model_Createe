import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { toast } from "../toast";

// 실행·데이터셋 삭제 확인 창(ADD-UX-02·07). 서버 미리보기로 함께 지워질 항목을 보여 주고,
// 함께 지워지는 것이 있으면 체크해야 삭제할 수 있다.
type Target = { kind: "run"; id: string; label: string } | { kind: "dataset"; id: string; label: string };

const mb = (b: number) => (b >= 1024 * 1024 ? `${(b / 1024 / 1024).toFixed(1)}MB` : `${Math.max(1, Math.round(b / 1024))}KB`);

export function DeleteDialog({ target, onClose, onDeleted }: { target: Target; onClose: () => void; onDeleted: () => void }) {
  const [pv, setPv] = useState<Record<string, any> | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [agree, setAgree] = useState(false);
  const [busy, setBusy] = useState(false);

  useEffect(() => {
    let alive = true;
    (target.kind === "run" ? api.runDeletePreview(target.id) : api.datasetDeletePreview(target.id))
      .then((p) => alive && setPv(p))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "삭제 정보를 불러오지 못했습니다"));
    return () => {
      alive = false;
    };
  }, [target.kind, target.id]);

  const extra =
    pv == null
      ? 0
      : target.kind === "run"
        ? pv.applications
        : pv.runs;
  const needAgree = extra > 0;
  const go = async () => {
    setBusy(true);
    setError(null);
    try {
      const out =
        target.kind === "run"
          ? await api.deleteRun(target.id, { confirmed: true, include_applications: agree })
          : await api.deleteDataset(target.id, { confirmed: true, include_runs: agree });
      const left = (out.files_left as string[]) ?? [];
      toast(
        left.length
          ? `삭제했습니다. 일부 파일은 사용 중이라 남았습니다: ${left.join(", ")}`
          : target.kind === "run"
            ? "실행을 삭제했습니다"
            : "데이터셋을 삭제했습니다",
        left.length ? "warn" : "ok",
      );
      onDeleted();
    } catch (e) {
      setError(e instanceof ApiError ? e.message : "삭제하지 못했습니다");
      setBusy(false);
    }
  };

  return (
    <div className="modal-back" role="presentation" onClick={onClose}>
      <div className="modal" role="alertdialog" aria-modal="true" aria-labelledby="del-title" onClick={(e) => e.stopPropagation()}>
        <h2 id="del-title" className="panel-title">
          {target.kind === "run" ? "실행 삭제" : "데이터셋 삭제"}
        </h2>
        <p>
          <strong>{target.label}</strong>
          {target.kind === "run"
            ? "의 모델·평가 예측·로그를 삭제합니다. 되돌릴 수 없습니다."
            : "의 원본·분석 결과·목표·전처리·학습 설정·확정 기록을 삭제합니다. 되돌릴 수 없습니다."}
        </p>
        {!pv && !error && <p className="muted">확인 중…</p>}
        {pv && !pv.deletable && <p className="field-error">{pv.reason_ko}</p>}
        {pv && pv.deletable && (
          <>
            {needAgree && (
              <label className="check del-agree">
                <input type="checkbox" checked={agree} onChange={(e) => setAgree(e.target.checked)} />
                {target.kind === "run"
                  ? `이 모델로 만든 새 데이터 적용 기록 ${pv.applications}개(예측 결과 CSV)도 함께 삭제합니다`
                  : `학습 실행 ${pv.runs}개와 모델·예측 결과${pv.applications ? `, 새 데이터 적용 기록 ${pv.applications}개` : ""}도 함께 삭제합니다`}
              </label>
            )}
            <p className="muted small">지울 파일 약 {mb(pv.bytes ?? 0)}</p>
          </>
        )}
        {error && <p className="field-error">{error}</p>}
        <div className="actions">
          <button
            type="button"
            className="btn btn-danger"
            disabled={!pv || !pv.deletable || busy || (needAgree && !agree)}
            onClick={() => void go()}
          >
            {busy ? "삭제 중" : "삭제"}
          </button>
          <button type="button" className="btn" autoFocus onClick={onClose}>
            취소
          </button>
        </div>
      </div>
    </div>
  );
}
