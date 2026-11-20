import { useState } from "react";
import { IconUpload } from "./Icons";

// .xls·.xlsm은 서버가 지원 안내와 함께 거절한다(다시 저장할 형식을 알려 줌)
const ACCEPT = /\.(csv|tsv|txt|xlsx|xls|xlsm|zip)$/i;

export function UploadPanel(props: {
  emphasized: boolean;
  onPick: () => void;
  onDropFiles: (files: File[]) => void;
  onRegisterFolder?: (path: string) => Promise<string | null>; // 오류 문구 또는 null
}) {
  const [over, setOver] = useState(false);
  const [rejected, setRejected] = useState<string | null>(null);
  const [folderOpen, setFolderOpen] = useState(false);
  const [folder, setFolder] = useState("");
  const [folderErr, setFolderErr] = useState<string | null>(null);
  const [folderBusy, setFolderBusy] = useState(false);
  const submitFolder = async () => {
    if (!props.onRegisterFolder) return;
    setFolderBusy(true);
    const e = await props.onRegisterFolder(folder);
    setFolderBusy(false);
    setFolderErr(e);
    if (!e) {
      setFolder("");
      setFolderOpen(false);
    }
  };

  return (
    <div
      className={`dropzone${over ? " is-over" : ""}${props.emphasized ? " is-empty" : ""}`}
      onDragOver={(e) => {
        e.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={(e) => {
        e.preventDefault();
        setOver(false);
        const files = Array.from(e.dataTransfer.files);
        const ok = files.filter((f) => ACCEPT.test(f.name));
        setRejected(ok.length < files.length ? "CSV(.csv·.tsv·.txt), 엑셀(.xlsx), 이미지 ZIP(.zip) 파일만 올릴 수 있습니다" : null);
        if (ok.length) props.onDropFiles(ok);
      }}
    >
      <h2 id="upload-title" className="section-title">
        파일 올리기
      </h2>
      <p className="dropzone-text">
        CSV 또는 엑셀(.xlsx) 파일, 이미지 ZIP을 여기에 끌어다 놓거나 선택하세요. 엑셀은 올린 뒤 시트를 고릅니다. 분석은 이 PC에서만 합니다.
      </p>
      <button type="button" className="btn btn-primary" onClick={props.onPick}>
        <IconUpload />
        파일 선택 (CSV·엑셀·ZIP)
      </button>
      {props.onRegisterFolder && (
        <button type="button" className="btn btn-quiet btn-small folder-btn" aria-expanded={folderOpen} onClick={() => setFolderOpen((v) => !v)}>
          이미지 폴더 등록
        </button>
      )}
      {folderOpen && (
        <form
          className="folder-form"
          onSubmit={(e) => {
            e.preventDefault();
            void submitFolder();
          }}
        >
          <label htmlFor="folder-path" className="muted small">
            이 PC의 이미지 폴더 전체 경로(복사하지 않고 그 자리에서 읽기만 합니다)
          </label>
          <input id="folder-path" value={folder} placeholder="예: C:\data\mvtec\bottle" onChange={(e) => setFolder(e.target.value)} />
          <button type="submit" className="btn btn-small btn-primary" disabled={!folder.trim() || folderBusy}>
            {folderBusy ? "등록 중" : "등록"}
          </button>
          {folderErr && (
            <p className="field-error" role="alert">
              {folderErr}
            </p>
          )}
        </form>
      )}
      {rejected && (
        <p className="field-error" role="alert">
          {rejected}
        </p>
      )}
    </div>
  );
}
