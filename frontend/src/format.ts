import type { ColumnDtype, DatasetState } from "./types";

export const nf = new Intl.NumberFormat("ko-KR");

export const DTYPE_KO: Record<ColumnDtype, string> = {
  datetime: "시간",
  numeric: "수치",
  categorical: "범주",
  text: "텍스트",
  empty: "비어 있음",
  excluded: "제외",
};

export const STATE_KO: Record<DatasetState | "LOCAL_UPLOADING", string> = {
  LOCAL_UPLOADING: "업로드 중",
  UPLOADING: "업로드 중",
  REGISTERED: "분석 대기",
  PROFILING: "분석 중",
  PROFILED: "완료",
  UPLOAD_FAILED: "업로드 실패",
  PROFILE_FAILED: "분석 실패",
};

export type Tone = "busy" | "done" | "fail";

export function toneOf(state: DatasetState | "LOCAL_UPLOADING"): Tone {
  if (state === "PROFILED") return "done";
  if (state === "UPLOAD_FAILED" || state === "PROFILE_FAILED") return "fail";
  return "busy";
}

export const isBusy = (s: DatasetState) => s === "UPLOADING" || s === "REGISTERED" || s === "PROFILING";

export function pct(x: number): string {
  if (x === 0) return "0%";
  if (x < 0.001) return "0.1% 미만";
  return `${(x * 100).toFixed(x < 0.1 ? 1 : 0)}%`;
}

export function bytes(n: number | null): string {
  if (n == null) return "";
  if (n < 1024) return `${n}B`;
  if (n < 1024 ** 2) return `${(n / 1024).toFixed(1)}KB`;
  return `${(n / 1024 ** 2).toFixed(1)}MB`;
}

export function elapsed(sinceIso: string | null, nowMs: number): string {
  if (!sinceIso) return "";
  const t = Date.parse(sinceIso.endsWith("Z") || sinceIso.includes("+") ? sinceIso : sinceIso + "Z");
  const s = Math.max(0, Math.round((nowMs - t) / 1000));
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, "0")}`;
}

const UNIT_KO: Record<string, string> = { s: "초", min: "분", h: "시간", d: "일" };

export function intervalKo(label: string | null): string {
  if (!label) return "";
  const m = label.match(/^(\d+(?:\.\d+)?)(s|min|h|d)$/);
  return m ? `${m[1]}${UNIT_KO[m[2]]}` : label;
}

export function dateTimeKo(iso: string | null): string {
  if (!iso) return "";
  return iso.replace("T", " ").replace(/:00$/, "");
}
