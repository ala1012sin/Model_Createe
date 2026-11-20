import { useCallback, useEffect, useRef, useState } from "react";
import { SideNav } from "./components/SideNav";
import { DatasetsPage } from "./components/DatasetsPage";
import { PreprocessPage } from "./components/PreprocessPage";
import { TrainingPage } from "./components/TrainingPage";
import { ConfirmPage } from "./components/ConfirmPage";
import { RunPage, RunsPage } from "./components/RunPages";
import { ApplyPage } from "./components/ApplyPage";
import { WorkbenchBar } from "./components/WorkbenchBar";
import { api } from "./api";
import { Toaster } from "./toast";

type Page = "datasets" | "preprocess" | "train" | "confirm" | "runs" | "apply";
// runs 화면의 id는 실행 ID(run_id), 나머지는 데이터셋 ID
type Route = { page: Page; datasetId: string | null; runId?: string | null };

/** 편집 화면이 알려 주는 저장 상태. 저장하지 않은 변경이 있을 때 다른 화면으로 가기 전에 묻는다. */
export interface EditGuard {
  dirty: boolean;
  save: () => Promise<boolean>;
}

// #/datasets/<id>, #/preprocess/<id>, #/train/<id>, #/confirm/<id> — 새로고침·메뉴 이동 후에도 같은 데이터셋을 유지
function parseHash(): Route {
  const [, page, id] = window.location.hash.replace(/^#/, "").split("/");
  if (page === "runs") return { page: "runs", datasetId: null, runId: id ? decodeURIComponent(id) : null };
  // apply의 id: run_… = 미리 고른 모델, app_… = 적용 기록
  if (page === "apply") return { page: "apply", datasetId: null, runId: id ? decodeURIComponent(id) : null };
  return {
    page: page === "preprocess" || page === "train" || page === "confirm" ? page : "datasets",
    datasetId: id ? decodeURIComponent(id) : null,
  };
}

export function hrefFor(page: Page, id: string | null | undefined): string {
  return `#/${page}${id ? `/${encodeURIComponent(id)}` : ""}`;
}

export default function App() {
  const [route, setRoute] = useState<Route>(parseHash);
  // 실행·결과 화면에서 다른 메뉴로 돌아갈 때 쓸 마지막 데이터셋
  const lastDs = useRef<string | null>(route.datasetId);
  if (route.datasetId) lastDs.current = route.datasetId;
  const [fallbackDs, setFallbackDs] = useState<string | null>(null);
  const adoptDataset = useCallback((id: string) => {
    lastDs.current = id;
    setFallbackDs(id);
  }, []);
  const dsId = route.datasetId ?? (route.page === "runs" || route.page === "apply" ? lastDs.current ?? fallbackDs : null);
  const [goalSaved, setGoalSaved] = useState<boolean | null>(null);
  const [query, setQuery] = useState("");
  const [leavePrompt, setLeavePrompt] = useState<{ saving: boolean; error?: string } | null>(null);
  const guard = useRef<EditGuard | null>(null);

  useEffect(() => {
    const on = () => setRoute(parseHash());
    window.addEventListener("hashchange", on);
    return () => window.removeEventListener("hashchange", on);
  }, []);

  const select = useCallback((id: string | null) => {
    const next = hrefFor("datasets", id);
    if (window.location.hash !== next) window.history.replaceState(null, "", next);
    setRoute({ page: "datasets", datasetId: id });
  }, []);

  const checkGoal = useCallback(() => {
    if (!dsId || dsId.startsWith("local-")) {
      setGoalSaved(null);
      return;
    }
    api
      .getGoal(dsId)
      .then((g) => setGoalSaved(g.saved))
      .catch(() => setGoalSaved(false));
  }, [dsId]);

  useEffect(checkGoal, [checkGoal]);
  useEffect(() => setLeavePrompt(null), [route.page]);

  const setGuard = useCallback((g: EditGuard | null) => {
    guard.current = g;
  }, []);

  const unlocked = goalSaved && dsId;
  const prepHref = unlocked ? hrefFor("preprocess", dsId) : null;
  const trainHref = unlocked ? hrefFor("train", dsId) : null;
  const datasetsHref = hrefFor("datasets", dsId);

  // 상단 검색: 데이터셋 화면에서는 입력과 함께 목록을 거르고,
  // 편집 화면에서는 입력하는 동안 머무르다가 Enter로 확정하면 데이터셋 화면으로 이동한다.
  const goDatasets = () => {
    setLeavePrompt(null);
    guard.current = null;
    window.location.hash = datasetsHref;
  };
  const onSearchSubmit = () => {
    if (route.page === "datasets") return;
    if (guard.current?.dirty) setLeavePrompt({ saving: false });
    else goDatasets();
  };
  const saveAndGo = async () => {
    setLeavePrompt({ saving: true });
    const ok = await (guard.current?.save() ?? Promise.resolve(true));
    if (ok) goDatasets();
    else setLeavePrompt({ saving: false, error: "저장하지 못했습니다. 화면의 오류를 확인하거나 변경을 버리고 이동하세요." });
  };

  return (
    <>
      <WorkbenchBar query={query} onQueryChange={setQuery} onSubmit={onSearchSubmit} />
      <Toaster />
      <div className="shell">
        <SideNav
          current={route.page === "confirm" ? "train" : route.page}
          links={{ datasets: datasetsHref, preprocess: prepHref, train: trainHref, runs: "#/runs", apply: "#/apply" }}
          lockedHint={{ preprocess: "목표 저장 후 사용", train: "목표 저장 후 사용" }}
        />
        <main className="main" id="main">
          {leavePrompt && (
            <div className="banner banner-warn leave-prompt" role="alertdialog" aria-labelledby="leave-title">
              <p id="leave-title">
                저장하지 않은 변경이 있습니다. 데이터셋 화면에서 ‘{query.trim()}’ 검색 결과를 보기 전에 어떻게 할까요?
              </p>
              {leavePrompt.error && <p className="field-error">{leavePrompt.error}</p>}
              <div className="actions">
                <button type="button" className="btn btn-small btn-primary" disabled={leavePrompt.saving} onClick={() => void saveAndGo()}>
                  {leavePrompt.saving ? "저장 중" : "저장하고 이동"}
                </button>
                <button type="button" className="btn btn-small" onClick={goDatasets}>
                  버리고 이동
                </button>
                <button type="button" className="btn btn-small btn-quiet" onClick={() => setLeavePrompt(null)}>
                  취소
                </button>
              </div>
            </div>
          )}
          {route.page === "preprocess" && route.datasetId ? (
            <PreprocessPage
              datasetId={route.datasetId}
              datasetsHref={datasetsHref}
              trainHref={hrefFor("train", route.datasetId)}
              onGuard={setGuard}
            />
          ) : route.page === "train" && route.datasetId ? (
            <TrainingPage
              datasetId={route.datasetId}
              datasetsHref={datasetsHref}
              preprocessHref={hrefFor("preprocess", route.datasetId)}
              confirmHref={hrefFor("confirm", route.datasetId)}
              onGuard={setGuard}
            />
          ) : route.page === "apply" ? (
            <ApplyPage key={route.runId ?? "none"} preselect={route.runId ?? null} onDataset={adoptDataset} />
          ) : route.page === "runs" ? (
            route.runId ? <RunPage runId={route.runId} /> : <RunsPage trainHref={trainHref} />
          ) : route.page === "confirm" && route.datasetId ? (
            <ConfirmPage
              datasetId={route.datasetId}
              hrefs={{
                goal: datasetsHref,
                preprocess: hrefFor("preprocess", route.datasetId),
                train: hrefFor("train", route.datasetId),
              }}
            />
          ) : (
            <DatasetsPage
              query={query}
              selected={route.datasetId}
              onSelect={select}
              onGoalSaved={checkGoal}
              preprocessHref={prepHref}
            />
          )}
        </main>
      </div>
    </>
  );
}
