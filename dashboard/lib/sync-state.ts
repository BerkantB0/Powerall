type SyncState = {
  queue?: { state: string; available_at: number; retry?: boolean } | null;
  run: { status: string; started: number } | null;
};

export function refreshState(sync: SyncState | undefined, now = Date.now() / 1000) {
  const queue = sync?.queue;
  // Older API replicas do not provide retry; infer it from their last failure.
  const retry = queue?.state === "queued" && (queue.retry ?? sync?.run?.status === "failed");
  const busy = queue ? queue.state === "running" || (queue.state === "queued" && !retry) : sync?.run?.status === "running";
  return {
    retry: Boolean(retry), busy: Boolean(busy),
    retryWait: retry && sync?.run ? Math.max(0, Math.ceil(sync.run.started + 60 - now)) : 0,
    retryMinutes: queue ? Math.max(0, Math.ceil((queue.available_at - now) / 60)) : 0,
  };
}
