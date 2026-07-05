/**
 * Decides *when* a layer's tile URL must be re-minted:
 *  - proactively at 75 % of the mint's lifetime (GEE URLs die at ~4 h), and
 *  - reactively on a burst of tile errors (expiry arrived early),
 * with a minimum gap since the last successful mint so that non-expiry
 * failures (offline, GEE quota storms) cannot trigger a mint storm through
 * the shared EE concurrency budget.
 *
 * Pure and timer-injectable — fully unit-tested without a map.
 */
import { REMINT_FRACTION, remintAtMs } from "../lib/time";

export interface RemintSchedulerOptions {
  onRemint: () => void;
  /** Sliding window for counting tile errors. */
  errorWindowMs?: number;
  /** Errors within the window that force an immediate re-mint. */
  errorThreshold?: number;
  /** Minimum age of the current mint before an error burst may re-mint. */
  minRemintGapMs?: number;
  fraction?: number;
  // Injectable clock/timers for tests.
  now?: () => number;
  schedule?: (fn: () => void, delayMs: number) => unknown;
  cancel?: (handle: unknown) => void;
}

export interface RemintScheduler {
  /** Call after every successful mint; (re)arms the 75 % timer. */
  noteMint(mintedAtMs: number, expiresAtMs: number): void;
  /** Call for every failed tile request of this layer's source. */
  noteTileError(): void;
  dispose(): void;
}

export function createRemintScheduler(options: RemintSchedulerOptions): RemintScheduler {
  const {
    onRemint,
    errorWindowMs = 10_000,
    errorThreshold = 3,
    minRemintGapMs = 30_000,
    fraction = REMINT_FRACTION,
    now = () => Date.now(),
    schedule = (fn, delayMs) => setTimeout(fn, delayMs),
    cancel = (handle) => clearTimeout(handle as ReturnType<typeof setTimeout>),
  } = options;

  let timer: unknown = null;
  let errorTimes: number[] = [];
  let lastMintAt = Number.NEGATIVE_INFINITY;
  // One re-mint in flight at a time; cleared by the next noteMint.
  let awaitingMint = false;

  const fire = () => {
    if (awaitingMint) return;
    awaitingMint = true;
    onRemint();
  };

  return {
    noteMint(mintedAtMs, expiresAtMs) {
      awaitingMint = false;
      lastMintAt = mintedAtMs;
      errorTimes = [];
      if (timer !== null) cancel(timer);
      const fireAt = remintAtMs(mintedAtMs, expiresAtMs, fraction);
      timer = schedule(fire, Math.max(0, fireAt - now()));
    },

    noteTileError() {
      if (awaitingMint) return;
      const t = now();
      errorTimes = errorTimes.filter((ts) => t - ts <= errorWindowMs);
      errorTimes.push(t);
      if (errorTimes.length >= errorThreshold && t - lastMintAt >= minRemintGapMs) {
        errorTimes = [];
        fire();
      }
    },

    dispose() {
      if (timer !== null) cancel(timer);
      timer = null;
      awaitingMint = true; // block any late noteTileError from firing
    },
  };
}
