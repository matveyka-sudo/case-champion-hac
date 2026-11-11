import type { Schemas } from "../types";

type Job = Schemas["JobAccepted"] | Schemas["JobView"];

export function validateJobProgress(job: Schemas["JobView"]) {
  const progress = job.progress;
  if (!progress) return;
  const { completed_batches: completed, total_batches: total, phase } = progress;
  if (completed > total ||
    (phase === "routing" && (completed >= total || job.status !== "running")) ||
    (phase === "finalizing" && (completed !== total || job.status !== "running")) ||
    (phase === "published" && (completed !== total || job.status !== "succeeded")) ||
    (phase === "stopped" && job.status !== "failed" && job.status !== "cancelled"))
    throw new Error("API_CONTRACT_INVALID");
}

export function jobProgressText(job: Job | null, localPhase: string) {
  if (!job || !("progress" in job) || !job.progress) return "";
  const progress = job.progress;
  const count = `Пакеты ${progress.completed_batches} из ${progress.total_batches}`;
  if (localPhase === "running") {
    if (job.status === "running" && progress.phase === "routing") return `${count} · Расчёт маршрутов`;
    if (job.status === "running" && progress.phase === "finalizing") return `${count} · Сохранение результата`;
    if (job.status === "succeeded" && progress.phase === "published") return `${count} · Получение сохранённого результата`;
  }
  if (progress.phase === "stopped" && ((job.status === "failed" && (localPhase === "running" || localPhase === "failed")) ||
    (job.status === "cancelled" && (localPhase === "running" || localPhase === "cancelled")))) return `${count} · Расчёт остановлен`;
  return "";
}
