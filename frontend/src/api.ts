import type {
  ApiErrorBody,
  ApplyRecord,
  ImageProfile,
  VisionGoal,
  VisionGoalOut,
  CompareSeries,
  CompareView,
  ConfirmationView,
  ModelItem,
  ModelRequirements,
  RunView,
  DatasetList,
  DatasetSummary,
  Goal,
  GoalOut,
  LocalProfile,
  PlanView,
  RecommendationOut,
  StepIn,
  TrainingFields,
  TrainingView,
  ValidationIssue,
} from "./types";

export class ApiError extends Error {
  constructor(public status: number, public body: ApiErrorBody) {
    super(body.message_ko);
  }
}

const FALLBACK: ApiErrorBody = { code: "NETWORK", message_ko: "서버에 연결할 수 없습니다. 앱이 실행 중인지 확인해 주세요" };

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  let res: Response;
  try {
    res = await fetch(path, init);
  } catch {
    throw new ApiError(0, FALLBACK);
  }
  const body = await res.json().catch(() => null);
  if (!res.ok) throw new ApiError(res.status, body && body.message_ko ? body : FALLBACK);
  return body as T;
}

export const api = {
  list: () => request<DatasetList>("/api/datasets"),
  get: (id: string) => request<DatasetSummary>(`/api/datasets/${encodeURIComponent(id)}`),
  profile: (id: string) => request<LocalProfile>(`/api/datasets/${encodeURIComponent(id)}/profile`),
  chooseSheet: (id: string, sheet: string) =>
    request<DatasetSummary>(`/api/datasets/${encodeURIComponent(id)}/sheet`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sheet }),
    }),
  chooseApplicationSheet: (id: string, sheet: string) =>
    request<ApplyRecord>(`/api/applications/${encodeURIComponent(id)}/sheet`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ sheet }),
    }),
  reprofile: (id: string) => request<DatasetSummary>(`/api/datasets/${encodeURIComponent(id)}/profile`, { method: "POST" }),
  getGoal: (id: string) => request<GoalOut>(`/api/datasets/${encodeURIComponent(id)}/goal`),
  saveGoal: (id: string, goal: Goal) =>
    request<GoalOut>(`/api/datasets/${encodeURIComponent(id)}/goal`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(goal),
    }),
  plan: (id: string) => request<PlanView>(`/api/datasets/${encodeURIComponent(id)}/plan`),
  recommend: (id: string) =>
    request<RecommendationOut>(`/api/datasets/${encodeURIComponent(id)}/recommendation`, { method: "POST" }),
  applyRecommendation: (id: string, recommendationId: string, expectedRevision: number) =>
    request<PlanView>(`/api/datasets/${encodeURIComponent(id)}/plan/apply`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ recommendation_id: recommendationId, expected_revision: expectedRevision }),
    }),
  savePlan: (id: string, body: { expected_revision: number; steps: StepIn[]; excluded: StepIn[]; source?: "manual" | "llm" }) =>
    request<PlanView>(`/api/datasets/${encodeURIComponent(id)}/plan`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  training: (id: string) => request<TrainingView>(`/api/datasets/${encodeURIComponent(id)}/training`),
  saveTraining: (id: string, body: TrainingFields & { expected_revision: number }) =>
    request<TrainingView>(`/api/datasets/${encodeURIComponent(id)}/training`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  validateTraining: (id: string, body: TrainingFields) =>
    request<{ issues: ValidationIssue[] }>(`/api/datasets/${encodeURIComponent(id)}/training/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  confirmation: (id: string) => request<ConfirmationView>(`/api/datasets/${encodeURIComponent(id)}/confirmation`),
  approve: (id: string, expectedRevision: number) =>
    request<ConfirmationView>(`/api/datasets/${encodeURIComponent(id)}/approve`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_revision: expectedRevision, confirmed: true }),
    }),
  startRun: (datasetId: string, planId: string, planHash: string, name?: string) =>
    request<RunView>(`/api/datasets/${encodeURIComponent(datasetId)}/runs`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ plan_id: planId, plan_hash: planHash, name: name?.trim() || null }),
    }),
  runs: () => request<{ items: RunView[] }>(`/api/runs`),
  run: (runId: string) => request<RunView>(`/api/runs/${encodeURIComponent(runId)}`),
  registerFolder: (path: string) =>
    request<DatasetSummary>(`/api/datasets/folder`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ path }),
    }),
  imageProfile: (id: string) => request<ImageProfile>(`/api/datasets/${encodeURIComponent(id)}/image-profile`),
  imageUrl: (id: string, path: string) => `/api/datasets/${encodeURIComponent(id)}/image?path=${encodeURIComponent(path)}`,
  visionGoal: (id: string) => request<VisionGoalOut>(`/api/datasets/${encodeURIComponent(id)}/vision-goal`),
  saveVisionGoal: (id: string, goal: VisionGoal) =>
    request<VisionGoalOut>(`/api/datasets/${encodeURIComponent(id)}/vision-goal`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(goal),
    }),
  renameRun: (runId: string, name: string | null) =>
    request<RunView>(`/api/runs/${encodeURIComponent(runId)}`, {
      method: "PATCH",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ name }),
    }),
  runDeletePreview: (runId: string) => request<Record<string, any>>(`/api/runs/${encodeURIComponent(runId)}/delete-preview`),
  deleteRun: (runId: string, body: { confirmed: boolean; include_applications: boolean }) =>
    request<Record<string, any>>(`/api/runs/${encodeURIComponent(runId)}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  datasetDeletePreview: (id: string) => request<Record<string, any>>(`/api/datasets/${encodeURIComponent(id)}/delete-preview`),
  deleteDataset: (id: string, body: { confirmed: boolean; include_runs: boolean }) =>
    request<Record<string, any>>(`/api/datasets/${encodeURIComponent(id)}`, {
      method: "DELETE",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
    }),
  compare: (runIds: string[]) =>
    request<CompareView>(`/api/runs/compare?${new URLSearchParams(runIds.map((r) => ["run_ids", r]))}`),
  compareSeries: (runIds: string[], horizon: string, group: string) =>
    request<CompareSeries>(
      `/api/runs/compare/series?${new URLSearchParams([...runIds.map((r) => ["run_ids", r]), ["horizon", horizon], ["group", group]])}`,
    ),
  cancelRun: async (runId: string) => {
    const { confirmation_id } = await request<{ confirmation_id: string }>(
      `/api/runs/${encodeURIComponent(runId)}/cancel-confirmation`,
      { method: "POST" },
    );
    return request<RunView>(`/api/runs/${encodeURIComponent(runId)}/cancel`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ confirmation_id }),
    });
  },
  models: () => request<{ items: ModelItem[] }>(`/api/models`),
  model: (runId: string) => request<ModelRequirements>(`/api/models/${encodeURIComponent(runId)}`),
  applications: () => request<{ items: ApplyRecord[] }>(`/api/applications`),
  application: (id: string) => request<ApplyRecord>(`/api/applications/${encodeURIComponent(id)}`),
  uploadApplication: (runId: string, file: File) =>
    request<ApplyRecord>(`/api/models/${encodeURIComponent(runId)}/applications`, {
      method: "POST",
      headers: { "Content-Type": "application/octet-stream", "X-File-Name": encodeURIComponent(file.name) },
      body: file,
    }),
  rebindApplication: (id: string, runId: string) =>
    request<ApplyRecord>(`/api/applications/${encodeURIComponent(id)}/rebind`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ run_id: runId }),
    }),
  predictApplication: (id: string) =>
    request<ApplyRecord>(`/api/applications/${encodeURIComponent(id)}/predict`, { method: "POST" }),
  applicationCsvHref: (id: string) => `/api/applications/${encodeURIComponent(id)}/predictions.csv`,
  predictionsHref: (runId: string) => `/api/runs/${encodeURIComponent(runId)}/predictions.csv`,
  validatePlan: (id: string, steps: StepIn[]) =>
    request<{ issues: ValidationIssue[] }>(`/api/datasets/${encodeURIComponent(id)}/plan/validate`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ steps }),
    }),
};

/** 업로드는 진행률 이벤트가 필요해 XHR을 쓴다. 브라우저가 전송량을 알 때만 onProgress가 비율을 받는다. */
export function uploadCsv(
  file: File,
  opts: { retryOf?: string; onProgress?: (ratio: number | null) => void },
): Promise<DatasetSummary> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    const q = opts.retryOf ? `?retry_of=${encodeURIComponent(opts.retryOf)}` : "";
    xhr.open("POST", `/api/datasets${q}`);
    xhr.setRequestHeader("Content-Type", "application/octet-stream");
    xhr.setRequestHeader("X-File-Name", encodeURIComponent(file.name));
    xhr.upload.onprogress = (e) => opts.onProgress?.(e.lengthComputable ? e.loaded / e.total : null);
    xhr.onload = () => {
      let body: unknown = null;
      try {
        body = JSON.parse(xhr.responseText);
      } catch {
        /* 무시 */
      }
      if (xhr.status >= 200 && xhr.status < 300) resolve(body as DatasetSummary);
      else reject(new ApiError(xhr.status, (body as ApiErrorBody) ?? FALLBACK));
    };
    xhr.onerror = () => reject(new ApiError(0, FALLBACK));
    xhr.send(file);
  });
}
