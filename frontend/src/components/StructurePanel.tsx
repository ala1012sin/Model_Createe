import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import type { DatasetSummary, LocalProfile, TimeCandidate } from "../types";
import { DTYPE_KO, STATE_KO, bytes, dateTimeKo, elapsed, intervalKo, nf, pct } from "../format";
import type { ListEntry } from "./DatasetList";
import { GoalForm } from "./GoalForm";
import { VisionPanel } from "./VisionPanel";
import { SheetPicker } from "./SheetPicker";

export function StructurePanel(props: {
  entry: ListEntry | null;
  summary: DatasetSummary | null;
  serverNow: number;
  hasAny: boolean;
  onRetryFile: (id: string) => void;
  onReprofile: (id: string) => Promise<void>;
  onGoalSaved: () => void;
  preprocessHref: string | null;
  onChooseSheet: (id: string, sheet: string) => Promise<void>;
  onDelete?: (id: string, name: string) => void;
}) {
  const { entry, summary } = props;
  const [choosing, setChoosing] = useState(false);
  const [sheetBusy, setSheetBusy] = useState(false);
  const [sheetError, setSheetError] = useState<string | null>(null);
  useEffect(() => {
    setChoosing(false);
    setSheetError(null);
  }, [summary?.dataset_id]);
  const choose = async (sheet: string) => {
    if (!summary) return;
    setSheetBusy(true);
    setSheetError(null);
    try {
      await props.onChooseSheet(summary.dataset_id, sheet);
      setChoosing(false);
    } catch (e) {
      setSheetError(e instanceof ApiError ? e.message : "시트를 고르지 못했습니다");
    } finally {
      setSheetBusy(false);
    }
  };
  const sheetNote = (s: DatasetSummary) =>
    s.selected_sheet
      ? "다른 시트를 고르면 그 시트만 다시 분석합니다. 이 데이터셋에 목표·설정이 저장되어 있으면 새 데이터셋으로 등록되고, 기존 목표·설정·승인·실행 결과는 그대로 남습니다."
      : "고른 시트만 분석합니다. 첫 번째 값이 있는 행을 열 이름(헤더)으로 씁니다.";
  const picker = (s: DatasetSummary, action: string, cancel?: () => void) => (
    <>
      <SheetPicker
        sheets={s.sheets ?? []}
        current={s.selected_sheet ?? null}
        busy={sheetBusy}
        action={action}
        note={sheetNote(s)}
        onChoose={(sh) => void choose(sh)}
        onCancel={cancel}
      />
      {sheetError && (
        <p className="field-error" role="alert">
          {sheetError}
        </p>
      )}
    </>
  );
  const [profile, setProfile] = useState<LocalProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const key = summary?.state === "PROFILED" && summary.modality !== "vision" ? `${summary.dataset_id}:${summary.profile_version}` : null;

  useEffect(() => {
    setProfile(null);
    setError(null);
    if (!key || !summary) return;
    let alive = true;
    api
      .profile(summary.dataset_id)
      .then((p) => alive && setProfile(p))
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "구조를 불러오지 못했습니다"));
    return () => {
      alive = false;
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  if (!entry) {
    return (
      <div className="panel-empty">
        <h2 className="panel-title">{props.hasAny ? "데이터셋을 선택하세요" : "CSV를 올리면 구조가 여기에 표시됩니다"}</h2>
        <p>행 수, 열마다 추정한 타입과 결측률, 시간열 후보와 측정 간격을 보여줍니다.</p>
        <p className="muted">파일 내용은 이 PC 밖으로 보내지 않습니다.</p>
      </div>
    );
  }

  const canDelete = !entry.local && !["UPLOADING", "PROFILING", "LOCAL_UPLOADING"].includes(entry.state);
  const header = (
    <header className="panel-head">
      <h2 className="panel-title">{entry.name}</h2>
      {summary?.size_bytes != null && <span className="muted">{bytes(summary.size_bytes)}</span>}
      {canDelete && props.onDelete && (
        <button type="button" className="btn btn-small btn-quiet panel-del" onClick={() => props.onDelete?.(entry.id, entry.name)}>
          데이터셋 삭제…
        </button>
      )}
    </header>
  );

  if (entry.state === "LOCAL_UPLOADING" || entry.state === "UPLOADING") {
    return (
      <div className="panel" aria-busy="true">
        {header}
        <p className="state-line">
          <span className="status status-busy">
            {STATE_KO[entry.state]}
            {entry.ratio != null ? ` ${Math.round(entry.ratio * 100)}%` : ""}
          </span>
          업로드가 끝나면 자동으로 분석합니다.
        </p>
        {entry.ratio != null && (
          <progress className="bar" max={1} value={entry.ratio} aria-label="업로드 진행률" />
        )}
      </div>
    );
  }

  if (entry.state === "REGISTERED" && summary?.needs_sheet) {
    return (
      <div className="panel">
        {header}
        <p className="state-line">
          <span className="status status-busy">시트 선택 필요</span>
          엑셀 파일을 받았습니다. 분석할 시트를 골라 주세요.
        </p>
        {picker(summary, "이 시트로 분석")}
      </div>
    );
  }

  if (entry.state === "REGISTERED" || entry.state === "PROFILING") {
    return (
      <div className="panel" aria-busy="true">
        {header}
        <p className="state-line">
          <span className="status status-busy">{STATE_KO[entry.state]}</span>
          서버에서 분석하고 있습니다. 경과 {elapsed(entry.since ?? null, props.serverNow)}
        </p>
        <ul className="steps" aria-label="분석 단계">
          {summary?.source_kind === "xlsx" && <li>시트 {summary.selected_sheet} 읽기·표로 정리</li>}
          <li>{summary?.source_kind === "xlsx" ? "정리한 표 읽기" : "인코딩·구분자 확인"}</li>
          <li>열 타입과 결측률 계산</li>
          <li>시간열 후보와 간격 추정</li>
        </ul>
        <p className="muted">분석하는 동안 다른 데이터셋을 선택해도 됩니다.</p>
      </div>
    );
  }

  if (entry.state === "UPLOAD_FAILED" || entry.state === "PROFILE_FAILED") {
    const err = summary?.error;
    const canReprofile = entry.state === "PROFILE_FAILED" && (err?.code === "INTERRUPTED" || summary?.source_kind === "xlsx");
    const isXlsx = summary?.source_kind === "xlsx" && !!summary.sheets?.length;
    return (
      <div className="panel">
        {header}
        <div className="fail-box" role="alert">
          <p className="fail-title">
            <span className="status status-fail">{STATE_KO[entry.state]}</span>
            {err?.message_ko ?? entry.errorText}
          </p>
          <p>파일을 고친 뒤 같은 데이터셋으로 다시 등록할 수 있습니다.</p>
          <div className="actions">
            {!entry.local && (
              <button type="button" className="btn btn-primary" onClick={() => props.onRetryFile(entry.id)}>
                수정한 파일로 다시 등록
              </button>
            )}
            {canReprofile && (
              <button type="button" className="btn btn-quiet" onClick={() => void props.onReprofile(entry.id)}>
                같은 파일로 다시 분석
              </button>
            )}
            {isXlsx && !choosing && (
              <button type="button" className="btn btn-quiet" onClick={() => setChoosing(true)}>
                다른 시트 고르기
              </button>
            )}
          </div>
          {isXlsx && summary?.selected_sheet && <p className="muted small">시트: {summary.selected_sheet}</p>}
          {isXlsx && choosing && summary && picker(summary, "이 시트로 분석", () => setChoosing(false))}
          {err && (
            <details className="details">
              <summary>오류 코드</summary>
              <code>{err.code}</code>
            </details>
          )}
        </div>
      </div>
    );
  }

  // 완료 — 이미지 데이터셋은 S1-V
  if (summary?.modality === "vision" && entry.state === "PROFILED") {
    return <VisionPanel key={summary.dataset_id} summary={summary} onDelete={props.onDelete ? () => props.onDelete?.(entry.id, entry.name) : undefined} />;
  }
  if (error) {
    return (
      <div className="panel">
        {header}
        <p className="inline-error" role="alert">
          {error}
        </p>
      </div>
    );
  }
  if (!profile) {
    return (
      <div className="panel" aria-busy="true">
        {header}
        <p className="muted">구조를 불러오는 중입니다.</p>
      </div>
    );
  }
  return (
    <ProfileView
      profile={profile}
      name={entry.name}
      size={summary?.size_bytes ?? null}
      datasetId={entry.id}
      onGoalSaved={props.onGoalSaved}
      preprocessHref={props.preprocessHref}
      onDelete={props.onDelete ? () => props.onDelete?.(entry.id, entry.name) : undefined}
      sheetBar={
        summary?.source_kind === "xlsx" && summary.sheets ? (
          <div className="sheet-bar">
            <p>
              시트 <strong>{summary.selected_sheet}</strong>
              <span className="muted small">
                {" "}
                ({summary.sheets.length}개 중)
                {summary.table && ` · 헤더 ${summary.table.header_row}행`}
                {summary.table && summary.table.rows_above_header + summary.table.blank_rows_skipped > 0
                  ? ` · 빈 행 ${summary.table.rows_above_header + summary.table.blank_rows_skipped}개 제외`
                  : ""}
                {summary.table && summary.table.empty_columns_dropped > 0 ? ` · 빈 열 ${summary.table.empty_columns_dropped}개 제외` : ""}
                {summary.table?.epoch === "1904" ? " · 1904 날짜 기준" : ""}
              </span>
              {!choosing && (
                <button type="button" className="btn btn-small btn-quiet" onClick={() => setChoosing(true)}>
                  다른 시트
                </button>
              )}
            </p>
            {summary.copied_from && <p className="muted small">같은 엑셀 파일의 다른 시트로 새로 등록한 데이터셋입니다.</p>}
            {summary.table && summary.table.formula_cells > 0 && (
              <p className="muted small">
                수식 {nf.format(summary.table.formula_cells)}칸은 파일에 저장된 계산 결과를 사용합니다(앱은 수식을 계산하지 않음).
              </p>
            )}
            {choosing && picker(summary, "이 시트로 분석", () => setChoosing(false))}
          </div>
        ) : null
      }
    />
  );
}

function TimeAxis({ t }: { t: TimeCandidate | undefined }) {
  if (!t) {
    return (
      <div className="axis axis-none">
        <p className="axis-label">시간열 후보 없음</p>
        <p className="muted">시간으로 읽히는 열을 찾지 못했습니다. 시계열 예측에 쓰려면 아래에서 시간열을 직접 고르세요.</p>
      </div>
    );
  }
  const iv = t.interval;
  return (
    <div className="axis" aria-label={`시간열 후보 ${t.name}`}>
      <p className="axis-label">
        시간열 후보 <strong>{t.name}</strong>
      </p>
      <div className="axis-track" aria-hidden="true">
        <span className="axis-tick" />
        <span className="axis-line" />
        <span className="axis-tick" />
      </div>
      <div className="axis-ends">
        <span>{dateTimeKo(t.start)}</span>
        <span className="axis-interval">
          {iv.status === "estimated"
            ? `${intervalKo(iv.label)} 간격`
            : iv.status === "irregular"
              ? `간격 불규칙 (중앙값 ${intervalKo(iv.label)})`
              : "간격 추정 불가"}
        </span>
        <span>{dateTimeKo(t.end)}</span>
      </div>
      <p className="axis-facts">
        {iv.status === "estimated" && iv.share != null && <>가장 흔한 간격이 {pct(iv.share)}를 차지합니다. </>}
        {iv.status !== "estimated" && iv.reason_ko && <>{iv.reason_ko} </>}
        같은 시각 {nf.format(t.duplicate_timestamps)}건, 순서가 거꾸로인 곳 {nf.format(t.backward_steps)}곳.
      </p>
    </div>
  );
}

function ProfileView({
  profile,
  name,
  size,
  datasetId,
  onGoalSaved,
  preprocessHref,
  sheetBar,
  onDelete,
}: {
  profile: LocalProfile;
  name: string;
  size: number | null;
  datasetId: string;
  onGoalSaved: () => void;
  preprocessHref: string | null;
  sheetBar?: React.ReactNode;
  onDelete?: () => void;
}) {
  const timeNames = new Set(profile.time_candidates.map((t) => t.name));
  return (
    <div className="panel">
      <header className="panel-head">
        <h2 className="panel-title">{name}</h2>
        <span className="status status-done">완료</span>
        {size != null && <span className="muted">{bytes(size)}</span>}
        {onDelete && (
          <button type="button" className="btn btn-small btn-quiet panel-del" onClick={onDelete}>
            데이터셋 삭제…
          </button>
        )}
      </header>
      {sheetBar}

      <div className="readout">
        <p className="shape" aria-label={`${profile.row_count}행 ${profile.column_count}열`}>
          <span className="shape-num">{nf.format(profile.row_count)}</span>
          <span className="shape-unit">행</span>
          <span className="shape-x" aria-hidden="true">
            ×
          </span>
          <span className="shape-num">{nf.format(profile.column_count)}</span>
          <span className="shape-unit">열</span>
        </p>
        <TimeAxis t={profile.time_candidates[0]} />
      </div>

      <dl className="facts">
        <div>
          <dt>중복 행</dt>
          <dd>{profile.duplicate_rows == null ? "검사 생략" : nf.format(profile.duplicate_rows)}</dd>
        </div>
        <div>
          <dt>제외한 행</dt>
          <dd>{nf.format(profile.malformed_rows)}</dd>
        </div>
        {sheetBar ? (
          <div>
            <dt>형식</dt>
            <dd>엑셀 시트</dd>
          </div>
        ) : (
          <>
            <div>
              <dt>인코딩</dt>
              <dd>{profile.encoding === "cp949" ? "CP949" : "UTF-8"}</dd>
            </div>
            <div>
              <dt>구분자</dt>
              <dd>{profile.delimiter === "\t" ? "탭" : profile.delimiter}</dd>
            </div>
          </>
        )}
      </dl>

      {profile.notices.length > 0 && (
        <ul className="notices">
          {profile.notices.map((n) => (
            <li key={n.code} className={`notice notice-${n.severity}`}>
              <span className="notice-kind">{n.severity === "warning" ? "주의" : "참고"}</span>
              {n.message_ko}
            </li>
          ))}
        </ul>
      )}

      <h3 className="sub-title" id="feat-title">
        피처 {profile.column_count}개
      </h3>
      <div className="table-wrap" role="region" aria-labelledby="feat-title" tabIndex={0}>
        <table className="feat">
          <thead>
            <tr>
              <th scope="col">이름</th>
              <th scope="col">타입</th>
              <th scope="col" className="num">
                결측률
              </th>
              <th scope="col" className="num">
                고유값
              </th>
              <th scope="col">비고</th>
            </tr>
          </thead>
          <tbody>
            {profile.columns.map((c) => (
              <tr key={c.alias}>
                <th scope="row" className="feat-name">
                  {c.name}
                </th>
                <td>
                  <span className={`dtype dtype-${c.dtype}`}>{DTYPE_KO[c.dtype]}</span>
                </td>
                <td className="num">
                  <span className="nullbar" aria-hidden="true">
                    <span style={{ width: `${Math.min(100, c.null_pct * 100)}%` }} />
                  </span>
                  {pct(c.null_pct)}
                </td>
                <td className="num">
                  {nf.format(c.distinct_count)}
                  {c.distinct_capped ? "+" : ""}
                </td>
                <td className="feat-note">
                  {timeNames.has(c.name) ? "시간열 후보" : ""}
                  {c.parse_fail_count > 0 ? `해석 안 된 값 ${nf.format(c.parse_fail_count)}개` : ""}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>

      <GoalForm datasetId={datasetId} profile={profile} onSaved={onGoalSaved} preprocessHref={preprocessHref} />
    </div>
  );
}
