/**
 * Subscribe to a job's server-sent event stream.
 *
 * Contract (see docs/phase2-execution-plan.md, "SSE wire format"): `points`
 * events are a progressive *preview* only — never replayed on reconnect — so
 * on `done` the subscriber MUST refetch the full result via the result
 * endpoint. That refetch is the caller's job (in `onDone`); this module just
 * dispatches typed events and closes on any terminal event.
 */
import type {
  JobDoneData,
  JobErrorData,
  JobFrameData,
  JobPointsData,
  JobProgressData,
} from "./types";

export interface JobHandlers {
  onProgress?: (data: JobProgressData) => void;
  onPoints?: (data: JobPointsData) => void;
  onFrame?: (data: JobFrameData) => void;
  onDone?: (data: JobDoneData) => void;
  onError?: (data: JobErrorData) => void;
}

/** Open an EventSource for a job; returns an unsubscribe/close function. */
export function subscribeJob(jobId: string, handlers: JobHandlers): () => void {
  const source = new EventSource(`/api/jobs/${jobId}/events`);
  const close = () => source.close();

  source.addEventListener("progress", (event) => {
    handlers.onProgress?.(JSON.parse(event.data) as JobProgressData);
  });
  source.addEventListener("points", (event) => {
    handlers.onPoints?.(JSON.parse(event.data) as JobPointsData);
  });
  source.addEventListener("frame", (event) => {
    handlers.onFrame?.(JSON.parse(event.data) as JobFrameData);
  });
  source.addEventListener("done", (event) => {
    handlers.onDone?.(JSON.parse(event.data) as JobDoneData);
    close();
  });
  // Our server sends a *named* "error" event carrying JSON; the browser also
  // fires a generic (data-less) "error" on connection drops. Only the former
  // is a job failure — distinguish by the presence of a data payload.
  source.addEventListener("error", (event) => {
    const message = event as MessageEvent<string>;
    if (!message.data) return; // transport hiccup; EventSource will reconnect
    handlers.onError?.(JSON.parse(message.data) as JobErrorData);
    close();
  });

  return close;
}
