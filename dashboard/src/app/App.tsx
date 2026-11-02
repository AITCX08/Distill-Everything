import { MissionControlPage, type ProgressSnapshot } from "../features/mission-control/MissionControlPage";
import { useMissionControl } from "../features/mission-control/useMissionControl";
import { CreateJobPage } from "../features/create-job/CreateJobPage";
import { LibraryPage } from "../features/library/LibraryPage";
import { LocalFilesPage } from "../features/local/LocalFilesPage";
import { PlatformsPage } from "../features/platforms/PlatformsPage";
import { JobHistoryPage } from "../features/job-history/JobHistoryPage";
import { ArtifactsPage } from "../features/artifacts/ArtifactsPage";
import { AppShell } from "./AppShell";
import { useWorkspace } from "./useWorkspace";

const EMPTY_PROGRESS_SNAPSHOT: ProgressSnapshot = {
  job_id: "",
  revision: 0,
  overall_progress: 0,
  coverage: 0,
  counts: {
    total: 0,
    active: 0,
    completed: 0,
    failed: 0,
    retry: 0,
    unsupported: 0,
    queued: 0,
    enumerated: 0,
  },
  eta_total_seconds: null,
  eta_active_slowest_seconds: null,
  provisional_eta: false,
  active_items: [],
};

export function App() {
  const mission = useMissionControl();
  const workspace = useWorkspace();

  return (
    <AppShell activeWorkspace={workspace}>
      {workspace === "mission" && <MissionControlPage
        snapshot={mission?.snapshot ?? EMPTY_PROGRESS_SNAPSHOT}
        job={mission?.job ?? null}
        traceEntries={mission?.traceEntries ?? []}
        onJobUpdated={mission?.updateJob}
        tasks={mission?.tasks ?? []}
        onTaskUpdated={mission?.updateTask}
        onViewArtifacts={() => { window.location.hash = "#artifacts"; }}
      />}
      {workspace === "library" && <LibraryPage />}
      {workspace === "local" && <LocalFilesPage />}
      {workspace === "create" && <CreateJobPage />}
      {workspace === "platforms" && <PlatformsPage />}
      {workspace === "history" && <JobHistoryPage />}
      {workspace === "artifacts" && <ArtifactsPage />}
    </AppShell>
  );
}
