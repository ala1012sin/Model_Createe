import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { ApiError, api, uploadCsv } from "../api";
import type { DatasetSummary } from "../types";
import { isBusy } from "../format";
import { UploadPanel } from "./UploadPanel";
import { DatasetList, type ListEntry } from "./DatasetList";
import { StructurePanel } from "./StructurePanel";
import { DeleteDialog } from "./DeleteDialog";

interface LocalUpload {
  tempId: string;
  name: string;
  ratio: number | null;
  retryOf?: string;
  error?: string;
}

let seq = 0;

export function DatasetsPage(props: {
  query: string;
  selected: string | null;
  onSelect: (id: string | null) => void;
  onGoalSaved: () => void;
  preprocessHref: string | null;
}) {
  const { selected, onSelect: setSelected } = props;
  const [items, setItems] = useState<DatasetSummary[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [clockOffset, setClockOffset] = useState(0); // 서버 시각 - 브라우저 시각
  const [uploads, setUploads] = useState<LocalUpload[]>([]);
  const [now, setNow] = useState(Date.now());
  const [announce, setAnnounce] = useState("");
  const [deleting, setDeleting] = useState<{ id: string; name: string } | null>(null);
  const fileInput = useRef<HTMLInputElement>(null);
  const retryTarget = useRef<string | null>(null);
  const selectedRef = useRef(selected);
  selectedRef.current = selected;

  const refresh = useCallback(async () => {
    try {
      const res = await api.list();
      setItems(res.items);
      setClockOffset(Date.parse(res.server_time) - Date.now());
      setLoadError(null);
      if (!selectedRef.current && res.items[0]) setSelected(res.items[0].dataset_id);
    } catch (e) {
      setLoadError(e instanceof ApiError ? e.message : "목록을 불러오지 못했습니다");
    } finally {
      setLoaded(true);
    }
  }, []);

  useEffect(() => {
    void refresh();
  }, [refresh]);

  // 진행 중인 작업이 있을 때만 1초마다 서버 상태를 다시 읽는다.
  const busy = uploads.some((u) => !u.error) || items.some((d) => isBusy(d.state) && !d.needs_sheet);
  useEffect(() => {
    if (!busy) return;
    const t = window.setInterval(() => {
      void refresh();
      setNow(Date.now());
    }, 1000);
    return () => window.clearInterval(t);
  }, [busy, refresh]);

  const startUpload = useCallback(
    (files: File[], retryOf?: string) => {
      for (const file of files) {
        const tempId = `local-${++seq}`;
        setUploads((u) => [{ tempId, name: file.name, ratio: null, retryOf }, ...u]);
        if (!retryOf) setSelected(tempId);
        setAnnounce(`${file.name} 업로드를 시작했습니다`);
        uploadCsv(file, {
          retryOf,
          onProgress: (ratio) => setUploads((u) => u.map((x) => (x.tempId === tempId ? { ...x, ratio } : x))),
        })
          .then(async (summary) => {
            await refresh();
            setUploads((u) => u.filter((x) => x.tempId !== tempId));
            const cur = selectedRef.current;
            if (cur === tempId || cur === retryOf) setSelected(summary.dataset_id);
          })
          .catch((e: unknown) => {
            const msg = e instanceof ApiError ? e.message : "업로드하지 못했습니다";
            setUploads((u) => u.map((x) => (x.tempId === tempId ? { ...x, error: msg } : x)));
            setAnnounce(`${file.name}: ${msg}`);
          });
      }
    },
    [refresh, setSelected],
  );

  const pickFile = (retryOf: string | null) => {
    retryTarget.current = retryOf;
    fileInput.current?.click();
  };

  const onFileChosen = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(e.target.files ?? []);
    e.target.value = "";
    if (!files.length) return;
    const retryOf = retryTarget.current ?? undefined;
    retryTarget.current = null;
    startUpload(retryOf ? files.slice(0, 1) : files, retryOf);
  };

  // 목록 표시용: 서버 항목 + 아직 응답이 오지 않은 로컬 업로드
  const entries: ListEntry[] = useMemo(() => {
    const retrying = new Map(uploads.filter((u) => u.retryOf && !u.error).map((u) => [u.retryOf!, u]));
    const pending: ListEntry[] = uploads
      .filter((u) => !u.retryOf || u.error)
      .map((u) => ({
        id: u.tempId,
        name: u.name,
        state: u.error ? "UPLOAD_FAILED" : "LOCAL_UPLOADING",
        ratio: u.ratio,
        errorText: u.error ?? null,
        local: true,
      }));
    const server: ListEntry[] = items.map((d) => {
      const r = retrying.get(d.dataset_id);
      return r
        ? { id: d.dataset_id, name: r.name, state: "LOCAL_UPLOADING", ratio: r.ratio, errorText: null, local: false }
        : {
            id: d.dataset_id,
            name: d.name,
            state: d.state,
            ratio: d.upload_progress,
            rows: d.row_count,
            cols: d.column_count,
            errorText: d.error?.message_ko ?? null,
            since: d.state_since,
            local: false,
            needsSheet: !!d.needs_sheet,
            images: d.modality === "vision" ? d.image_count ?? null : undefined,
            sheet: d.selected_sheet ?? null,
          };
    });
    return [...pending, ...server];
  }, [items, uploads]);

  const selectedEntry = entries.find((e) => e.id === selected) ?? null;
  const q = props.query.trim().toLocaleLowerCase();
  const visibleEntries = q ? entries.filter((e) => e.name.toLocaleLowerCase().includes(q)) : entries;
  const selectedSummary = items.find((d) => d.dataset_id === selected) ?? null;
  const serverNow = now + clockOffset;

  return (
    <div className="datasets">
      <h1 className="page-title">데이터셋</h1>
      <input
        ref={fileInput}
        type="file"
        accept=".csv,.tsv,.txt,text/csv,.xlsx,.xls,.xlsm,.zip,application/zip"
        multiple
        className="visually-hidden"
        tabIndex={-1}
        aria-hidden="true"
        onChange={onFileChosen}
      />
      <div className="datasets-grid">
        <section className="col-left" aria-labelledby="upload-title">
          <UploadPanel
            emphasized={loaded && entries.length === 0}
            onPick={() => pickFile(null)}
            onDropFiles={(files) => startUpload(files)}
            onRegisterFolder={async (path) => {
              try {
                const s = await api.registerFolder(path);
                await refresh();
                setSelected(s.dataset_id);
                setAnnounce(`${s.name} 폴더를 등록했습니다`);
                return null;
              } catch (e) {
                return e instanceof ApiError ? e.message : "폴더를 등록하지 못했습니다";
              }
            }}
          />
          <DatasetList
            entries={visibleEntries}
            filtering={!!q && entries.length > 0}
            selected={selected}
            serverNow={serverNow}
            loaded={loaded}
            loadError={loadError}
            onSelect={setSelected}
            onRetryFile={(id) => pickFile(id)}
            onReload={() => void refresh()}
          />
        </section>
        <section className="col-right" aria-label="선택한 데이터 구조">
          <StructurePanel
            entry={selectedEntry}
            summary={selectedSummary}
            serverNow={serverNow}
            hasAny={entries.length > 0}
            onRetryFile={(id) => pickFile(id)}
            onGoalSaved={props.onGoalSaved}
            preprocessHref={props.preprocessHref}
            onReprofile={async (id) => {
              await api.reprofile(id).catch(() => undefined);
              await refresh();
            }}
            onDelete={(id, name) => setDeleting({ id, name })}
            onChooseSheet={async (id, sheet) => {
              const s = await api.chooseSheet(id, sheet);
              await refresh();
              if (s.dataset_id !== id) {
                setSelected(s.dataset_id);
                setAnnounce(`시트 ${sheet}을(를) 새 데이터셋으로 등록했습니다`);
              }
            }}
          />
        </section>
      </div>
      <div className="visually-hidden" aria-live="polite">
        {announce}
      </div>
      {deleting && (
        <DeleteDialog
          target={{ kind: "dataset", id: deleting.id, label: deleting.name }}
          onClose={() => setDeleting(null)}
          onDeleted={() => {
            setDeleting(null);
            setSelected(null);
            setAnnounce(`${deleting.name}을(를) 삭제했습니다`);
            void refresh();
          }}
        />
      )}
    </div>
  );
}
