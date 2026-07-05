import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { subscribeJob } from "./sse";

type Listener = (event: { data?: string }) => void;

class MockEventSource {
  static instances: MockEventSource[] = [];
  readonly url: string;
  closed = false;
  private readonly listeners = new Map<string, Listener[]>();

  constructor(url: string) {
    this.url = url;
    MockEventSource.instances.push(this);
  }

  addEventListener(type: string, cb: Listener): void {
    const list = this.listeners.get(type) ?? [];
    list.push(cb);
    this.listeners.set(type, list);
  }

  close(): void {
    this.closed = true;
  }

  emit(type: string, data?: string): void {
    const event = data === undefined ? {} : { data };
    for (const cb of this.listeners.get(type) ?? []) cb(event);
  }
}

beforeEach(() => {
  MockEventSource.instances = [];
  vi.stubGlobal("EventSource", MockEventSource);
});

afterEach(() => {
  vi.unstubAllGlobals();
});

function only(): MockEventSource {
  expect(MockEventSource.instances).toHaveLength(1);
  return MockEventSource.instances[0]!;
}

describe("subscribeJob", () => {
  it("connects to the job's event endpoint", () => {
    subscribeJob("abc", {});
    expect(only().url).toBe("/api/jobs/abc/events");
  });

  it("dispatches progress and points to handlers", () => {
    const onProgress = vi.fn();
    const onPoints = vi.fn();
    subscribeJob("j", { onProgress, onPoints });
    const es = only();

    es.emit("progress", JSON.stringify({ done: 1, total: 3, message: "chunk 1" }));
    es.emit("points", JSON.stringify({ points: [{ date: "2024-01-01", value: 0.5, count: 10 }] }));

    expect(onProgress).toHaveBeenCalledWith({ done: 1, total: 3, message: "chunk 1" });
    expect(onPoints).toHaveBeenCalledWith({
      points: [{ date: "2024-01-01", value: 0.5, count: 10 }],
    });
    expect(es.closed).toBe(false); // still streaming
  });

  it("closes after a done event and reports the result", () => {
    const onDone = vi.fn();
    subscribeJob("j", { onDone });
    const es = only();
    es.emit("done", JSON.stringify({ status: "succeeded", result: { cache_key: "k" } }));
    expect(onDone).toHaveBeenCalledWith({ status: "succeeded", result: { cache_key: "k" } });
    expect(es.closed).toBe(true);
  });

  it("treats a named error event as a failure and closes", () => {
    const onError = vi.fn();
    subscribeJob("j", { onError });
    const es = only();
    es.emit("error", JSON.stringify({ status: "failed", detail: "boom" }));
    expect(onError).toHaveBeenCalledWith({ status: "failed", detail: "boom" });
    expect(es.closed).toBe(true);
  });

  it("ignores a data-less transport error (browser reconnect)", () => {
    const onError = vi.fn();
    subscribeJob("j", { onError });
    const es = only();
    es.emit("error"); // no data → connection hiccup, not a job failure
    expect(onError).not.toHaveBeenCalled();
    expect(es.closed).toBe(false);
  });

  it("closes the stream when unsubscribed", () => {
    const close = subscribeJob("j", {});
    close();
    expect(only().closed).toBe(true);
  });
});
