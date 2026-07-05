import { describe, expect, it } from "vitest";
import { createRemintScheduler, type RemintScheduler } from "./remintScheduler";

/** Deterministic clock + timer harness. */
class FakeClock {
  time = 0;
  private timers = new Map<number, { at: number; fn: () => void }>();
  private nextHandle = 1;

  now = () => this.time;
  schedule = (fn: () => void, delayMs: number) => {
    const handle = this.nextHandle++;
    this.timers.set(handle, { at: this.time + delayMs, fn });
    return handle;
  };
  cancel = (handle: unknown) => {
    this.timers.delete(handle as number);
  };

  advance(ms: number) {
    const target = this.time + ms;
    // Fire due timers in time order.
    for (;;) {
      const due = [...this.timers.entries()]
        .filter(([, t]) => t.at <= target)
        .sort((a, b) => a[1].at - b[1].at)[0];
      if (!due) break;
      const [handle, timer] = due;
      this.timers.delete(handle);
      this.time = timer.at;
      timer.fn();
    }
    this.time = target;
  }

  pendingCount() {
    return this.timers.size;
  }
}

function setup(overrides: { minRemintGapMs?: number } = {}) {
  const clock = new FakeClock();
  let fired = 0;
  const scheduler = createRemintScheduler({
    onRemint: () => {
      fired++;
    },
    now: clock.now,
    schedule: clock.schedule,
    cancel: clock.cancel,
    ...overrides,
  });
  return { clock, scheduler, fires: () => fired };
}

const HOUR = 3600_000;

/** A mint valid for 4 h starting at the clock's current time. */
function mint(scheduler: RemintScheduler, clock: FakeClock) {
  scheduler.noteMint(clock.time, clock.time + 4 * HOUR);
}

describe("remintScheduler timer", () => {
  it("fires exactly once at 75 % of the mint lifetime", () => {
    const { clock, scheduler, fires } = setup();
    mint(scheduler, clock);
    clock.advance(3 * HOUR - 1000);
    expect(fires()).toBe(0);
    clock.advance(1000);
    expect(fires()).toBe(1);
    clock.advance(10 * HOUR); // no further fires without a new mint
    expect(fires()).toBe(1);
  });

  it("a new mint reschedules and cancels the previous timer", () => {
    const { clock, scheduler, fires } = setup();
    mint(scheduler, clock);
    clock.advance(1 * HOUR);
    mint(scheduler, clock); // re-minted early (e.g. params changed)
    clock.advance(2.5 * HOUR); // old timer (at t=3h) must NOT fire
    expect(fires()).toBe(0);
    clock.advance(0.5 * HOUR); // new timer at 1h + 3h
    expect(fires()).toBe(1);
    expect(clock.pendingCount()).toBe(0);
  });

  it("dispose cancels the pending timer", () => {
    const { clock, scheduler, fires } = setup();
    mint(scheduler, clock);
    scheduler.dispose();
    clock.advance(10 * HOUR);
    expect(fires()).toBe(0);
  });
});

describe("remintScheduler error bursts", () => {
  it("3 errors within 10 s force an immediate re-mint", () => {
    const { clock, scheduler, fires } = setup();
    mint(scheduler, clock);
    clock.advance(60_000); // past the min gap
    scheduler.noteTileError();
    clock.advance(2000);
    scheduler.noteTileError();
    expect(fires()).toBe(0);
    clock.advance(2000);
    scheduler.noteTileError();
    expect(fires()).toBe(1);
  });

  it("errors spread wider than the window never accumulate", () => {
    const { clock, scheduler, fires } = setup();
    mint(scheduler, clock);
    clock.advance(60_000);
    for (let i = 0; i < 6; i++) {
      scheduler.noteTileError();
      clock.advance(11_000); // each error falls out of the 10 s window
    }
    expect(fires()).toBe(0);
  });

  it("the min-gap guard suppresses bursts right after a fresh mint", () => {
    const { clock, scheduler, fires } = setup();
    mint(scheduler, clock);
    clock.advance(5000); // < 30 s since mint: offline/quota, not expiry
    scheduler.noteTileError();
    scheduler.noteTileError();
    scheduler.noteTileError();
    expect(fires()).toBe(0);
  });

  it("no repeat fire while a re-mint is in flight; next mint re-arms", () => {
    const { clock, scheduler, fires } = setup();
    mint(scheduler, clock);
    clock.advance(60_000);
    scheduler.noteTileError();
    scheduler.noteTileError();
    scheduler.noteTileError();
    expect(fires()).toBe(1);
    // Still failing while the re-mint request runs: no storm.
    scheduler.noteTileError();
    scheduler.noteTileError();
    scheduler.noteTileError();
    expect(fires()).toBe(1);
    // Re-mint landed → armed again.
    mint(scheduler, clock);
    clock.advance(60_000);
    scheduler.noteTileError();
    scheduler.noteTileError();
    scheduler.noteTileError();
    expect(fires()).toBe(2);
  });

  it("errors do not leak across mints", () => {
    const { clock, scheduler, fires } = setup();
    mint(scheduler, clock);
    clock.advance(60_000);
    scheduler.noteTileError();
    scheduler.noteTileError();
    mint(scheduler, clock); // successful mint resets the window
    clock.advance(60_000);
    scheduler.noteTileError();
    expect(fires()).toBe(0);
  });
});
