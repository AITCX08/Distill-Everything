export type JobStatus = "queued" | "running" | "pause_requested" | "paused" | "partial" | "completed" | "failed";
export interface RuntimeModel { provider: string; model: string; }
export interface RuntimeModels { transcription: RuntimeModel; summary: RuntimeModel; }
export interface HealthResponse { status: string; api_version: string; static_compatible: boolean; models: RuntimeModels; }
export interface JobSummary { job_id: string; status: JobStatus; revision: number; platform: string; creator_name: string; total_items: number; completed_items: number; failed_items: number; unsupported_items: number; updated_at: string; }
export interface MissionJob {
  job_id: string;
  status: string;
  revision: number;
  display_title?: string;
  creator_name?: string;
  platform?: string;
  artifact_count?: number;
  completed_at?: string;
}
export interface JobItem { source_id: string; display_title: string; part_number: number | null; processing_status: string; retryable: boolean; stage_progress: number; overall_progress: number; last_error: string | null; completed_at: string | null; updated_at: string; }
export interface WorkerTransfer { completed_bytes: number; total_bytes: number; bytes_per_second: number; }
export interface WorkerTask {
  task_id: string;
  job_id: string;
  source_id: string;
  display_title: string;
  part_number: number | null;
  delivery_state: "pending" | "available" | "unavailable";
  status: string;
  stage: string;
  revision: number;
  attempt: number;
  checkpoint_revision: number;
  updated_at: string;
  completed_at?: string;
  transfer?: WorkerTransfer;
  error?: string;
}
export interface DashboardEvent { eventType: "snapshot" | "job.updated" | "item.updated" | "trace.appended"; data: Record<string, unknown>; }
