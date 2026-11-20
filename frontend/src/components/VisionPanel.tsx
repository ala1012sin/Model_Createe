import { useEffect, useMemo, useState } from "react";
import { ApiError, api } from "../api";
import type { DatasetSummary, ImageProfile, ImageRole, ValidationIssue, VisionGoal } from "../types";
import { toast } from "../toast";

// S1-V 이미지 구조 패널(V1, docs/P3_VISION_BRIEF.md): 요약 · 폴더 트리와 역할 · 예시 이미지 · 보정 · 태스크 → 비전 목표 저장.
export const ROLE_KO: Record<ImageRole, string> = {
  train_normal: "정상 학습",
  calibration_normal: "보정 정상",
  test_normal: "정상 평가",
  test_defect: "불량 평가",
  gt_mask: "GT 마스크",
  ignore: "제외(확인 필요)",
};
const ROLES = Object.keys(ROLE_KO) as ImageRole[];
const nf = new Intl.NumberFormat("ko-KR");
const bytes = (n: number) => (n >= 1024 * 1024 ? `${(n / 1024 / 1024).toFixed(1)}MB` : `${(n / 1024).toFixed(1)}KB`);

export function VisionPanel({ summary, onDelete }: { summary: DatasetSummary; onDelete?: () => void }) {
  const id = summary.dataset_id;
  const [ip, setIp] = useState<ImageProfile | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [roles, setRoles] = useState<Record<string, ImageRole>>({});
  const [calib, setCalib] = useState<VisionGoal["calibration"]>({ source: "split_from_train", fraction: 0.1, seed: 0 });
  const [issues, setIssues] = useState<ValidationIssue[]>([]);
  const [saved, setSaved] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [exIdx, setExIdx] = useState(0);

  useEffect(() => {
    let alive = true;
    setIp(null);
    setError(null);
    setIssues([]);
    setSaved(null);
    Promise.all([api.imageProfile(id), api.visionGoal(id)])
      .then(([p, g]) => {
        if (!alive) return;
        setIp(p);
        const guess = Object.fromEntries(p.folders.map((f) => [f.path, f.role_guess])) as Record<string, ImageRole>;
        if (g.saved && g.goal) {
          setRoles({ ...guess, ...g.goal.roles });
          setCalib(g.goal.calibration);
          setIssues(g.issues ?? []);
          setSaved("저장된 목표를 불러왔습니다");
        } else setRoles(guess);
      })
      .catch((e) => alive && setError(e instanceof ApiError ? e.message : "이미지 구조를 불러오지 못했습니다"));
    return () => {
      alive = false;
    };
  }, [id, summary.profile_version]);

  const counts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const f of ip?.folders ?? []) c[roles[f.path] ?? f.role_guess] = (c[roles[f.path] ?? f.role_guess] ?? 0) + f.images;
    return c;
  }, [ip, roles]);

  if (error) return <p className="inline-error" role="alert">{error}</p>;
  // 데이터셋을 바꾼 직후 첫 렌더에서 이전 데이터셋의 경로로 새 id 이미지를 요청하지 않도록(404) 소속을 확인한다.
  if (!ip || ip.dataset_id !== id) return <p className="muted">이미지 구조를 불러오는 중입니다.</p>;

  const train = counts.train_normal ?? 0;
  const nCal = calib.source === "split_from_train" ? (train ? Math.max(1, Math.round(train * calib.fraction)) : 0) : counts.calibration_normal ?? 0;
  const trainAfter = calib.source === "split_from_train" ? train - nCal : train;
  const masks = counts.gt_mask ?? 0;
  const normalEx = ip.folders.filter((f) => ["train_normal", "test_normal"].includes(roles[f.path])).flatMap((f) => f.samples);
  const defectEx = ip.folders.filter((f) => roles[f.path] === "test_defect").flatMap((f) => f.samples.map((s) => ({ s, kind: f.defect_type })));
  const ex = [
    normalEx.length ? { label: "정상", path: normalEx[exIdx % normalEx.length] } : null,
    defectEx.length ? { label: `불량 · ${defectEx[exIdx % defectEx.length].kind ?? ""}`, path: defectEx[exIdx % defectEx.length].s } : null,
  ].filter(Boolean) as { label: string; path: string }[];
  const top = ip.sizes[0];

  const save = async () => {
    setBusy(true);
    setSaved(null);
    try {
      const out = await api.saveVisionGoal(id, { task: "anomaly_detection", roles, calibration: calib });
      setIssues(out.issues ?? []);
      setSaved("목표를 저장했습니다");
      toast("이미지 목표(폴더 역할)를 저장했습니다");
    } catch (e) {
      if (e instanceof ApiError) setIssues((e.body.issues as ValidationIssue[]) ?? []);
      setSaved(null);
      setError(null);
      if (!(e instanceof ApiError)) toast("저장하지 못했습니다", "warn");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="panel vision">
      <header className="panel-head">
        <h2 className="panel-title">{summary.name}</h2>
        <span className="status status-done">완료</span>
        <span className="muted">{summary.source_kind === "image_folder" ? "로컬 폴더(읽기 전용)" : summary.size_bytes != null ? bytes(summary.size_bytes) : ""}</span>
        {onDelete && (
          <button type="button" className="btn btn-small btn-quiet panel-del" onClick={onDelete}>
            데이터셋 삭제…
          </button>
        )}
      </header>
      <p className="vis-summary">
        <strong>이미지 {nf.format(ip.image_count)}장</strong>
        {masks > 0 && <> · GT 마스크 {nf.format(masks)}장</>}
        {" · "}
        {Object.entries(ip.formats).map(([k, v]) => `${k} ${nf.format(v)}`).join(", ")}
        {top && <> · {top.w}×{top.h}{ip.sizes.length > 1 ? ` 외 ${ip.sizes.length - 1}가지` : ""}</>}
      </p>
      <p className="muted small">
        손상 {ip.integrity.corrupt ?? 0}장 / 표본 {ip.integrity.checked ?? 0}장 검사({ip.integrity.method})
        {ip.mask_pairs.defect_images ? ` · 불량-마스크 짝 ${ip.mask_pairs.paired}/${ip.mask_pairs.defect_images}` : ""}
        {ip.skipped.non_image ? ` · 이미지 아닌 파일 ${ip.skipped.non_image}개 건너뜀` : ""}
      </p>
      {ip.notices.map((n) => (
        <p key={n.code} className={n.severity === "warning" ? "field-warn" : "muted small"}>
          {n.message_ko}
        </p>
      ))}

      <div className="vis-grid">
        <section aria-labelledby="vis-folders">
          <h3 className="sub-title" id="vis-folders">
            폴더와 역할 <span className="muted small">(폴더 이름으로 추정 — 확인·수정 후 저장)</span>
          </h3>
          <div className="table-wrap">
            <table className="metric-table vis-folders">
              <thead>
                <tr>
                  <th scope="col">폴더</th>
                  <th scope="col" className="num">장수</th>
                  <th scope="col">역할</th>
                </tr>
              </thead>
              <tbody>
                {ip.folders.map((f) => (
                  <tr key={f.path} className={roles[f.path] !== f.role_guess ? "cmp-changed" : undefined}>
                    <td className="vis-path">{f.path}</td>
                    <td className="num">{nf.format(f.images)}</td>
                    <td>
                      <select aria-label={`${f.path} 역할`} value={roles[f.path] ?? f.role_guess}
                        onChange={(e) => {
                          setRoles({ ...roles, [f.path]: e.target.value as ImageRole });
                          setSaved(null);
                        }}>
                        {ROLES.map((r) => (
                          <option key={r} value={r}>
                            {ROLE_KO[r]}
                            {r === f.role_guess ? " (추정)" : ""}
                          </option>
                        ))}
                      </select>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
        <section aria-labelledby="vis-ex">
          <h3 className="sub-title" id="vis-ex">
            예시 이미지 <span className="muted small">(이 PC에서만 표시)</span>
          </h3>
          <div className="vis-examples">
            {ex.length === 0 && <p className="muted">정상·불량 역할 폴더를 고르면 예시가 보입니다.</p>}
            {ex.map((e) => (
              <figure key={e.label}>
                <img src={api.imageUrl(id, e.path)} alt={`${e.label} 예시 ${e.path}`} loading="lazy"
                  onError={(ev) => ((ev.target as HTMLImageElement).alt = "미리보기를 표시할 수 없는 형식입니다")} />
                <figcaption>
                  <strong>{e.label}</strong> <span className="muted small">{e.path}</span>
                </figcaption>
              </figure>
            ))}
          </div>
          {ex.length > 0 && (
            <button type="button" className="btn btn-small btn-quiet" onClick={() => setExIdx((n) => n + 1)}>
              다른 예시 보기
            </button>
          )}
        </section>
      </div>

      <section className="vis-goal" aria-labelledby="vis-goal-title">
        <h3 className="sub-title" id="vis-goal-title">
          목표
        </h3>
        <fieldset className="checks">
          <legend className="muted small">태스크</legend>
          <label className="check">
            <input type="radio" name="vis-task" checked readOnly /> 정상 기반 이상탐지(정상 이미지로만 학습)
          </label>
          <label className="check is-disabled" title="픽셀 라벨로 지도학습 — 아직 지원하지 않습니다">
            <input type="radio" name="vis-task" disabled /> 세그멘테이션(라벨 필요 · 준비 중)
          </label>
        </fieldset>
        <fieldset className="checks">
          <legend className="muted small">보정(판정 임계값) 정상 이미지</legend>
          <label className="check">
            <input type="radio" name="vis-cal" checked={calib.source === "split_from_train"}
              onChange={() => setCalib({ ...calib, source: "split_from_train" })} />
            정상 학습에서 떼어 내기
            <select aria-label="떼어 낼 비율" value={calib.fraction} disabled={calib.source !== "split_from_train"}
              onChange={(e) => setCalib({ ...calib, fraction: Number(e.target.value) })}>
              {[0.05, 0.1, 0.2, 0.3].map((v) => (
                <option key={v} value={v}>
                  {Math.round(v * 100)}%
                </option>
              ))}
            </select>
          </label>
          <label className="check">
            <input type="radio" name="vis-cal" checked={calib.source === "folder"} onChange={() => setCalib({ ...calib, source: "folder" })} />
            '보정 정상' 역할 폴더 사용
          </label>
        </fieldset>
        <p className="vis-counts" aria-live="polite">
          정상 학습 <strong>{nf.format(trainAfter)}</strong>장 · 보정 <strong>{nf.format(nCal)}</strong>장 · 정상 평가{" "}
          <strong>{nf.format(counts.test_normal ?? 0)}</strong>장 · 불량 평가 <strong>{nf.format(counts.test_defect ?? 0)}</strong>장 · GT 마스크{" "}
          <strong>{nf.format(masks)}</strong>장
          <span className="muted small"> — 학습·보정·평가는 서로 겹치지 않습니다</span>
        </p>
        {issues.map((i, n) => (
          <p key={n} className={i.severity === "error" ? "field-error" : i.severity === "warning" ? "field-warn" : "muted small"}>
            {i.message_ko}
            {i.fix_hint_ko ? ` — ${i.fix_hint_ko}` : ""}
          </p>
        ))}
        <div className="goal-actions">
          <button type="button" className="btn btn-primary" disabled={busy} onClick={() => void save()}>
            {busy ? "저장 중" : "목표 저장"}
          </button>
          {saved && (
            <p className="saved-text" role="status">
              {saved}
            </p>
          )}
        </div>
        <p className="muted small">전처리·학습 설정(PatchCore)은 비전 학습 단계에서 이어집니다.</p>
      </section>
    </div>
  );
}
