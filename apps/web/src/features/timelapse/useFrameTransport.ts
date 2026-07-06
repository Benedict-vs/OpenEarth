/**
 * Frame-sequence transport: preloads every frame image, then drives an
 * `onFrame(index, img)` callback from a requestAnimationFrame loop. The play
 * head lives in a ref so ticks never re-render React; component state updates
 * only on a *frame boundary* (~fps, not ~60 Hz) so the scrubber can follow.
 *
 * The studio player draws each frame to a canvas; the Explore map overlay
 * (stage 5) reuses the same hook and pushes frames into a MapLibre image
 * source — the transport itself is render-target agnostic.
 */
import { useCallback, useEffect, useRef, useState } from "react";
import { advanceIndex, frameDurationMs, framesElapsed, preloadComplete } from "../../lib/timelapse";

export interface FrameTransport {
  ready: boolean;
  loadedCount: number;
  total: number;
  index: number;
  playing: boolean;
  play(): void;
  pause(): void;
  toggle(): void;
  seek(index: number): void;
}

export interface FrameTransportOptions {
  fps: number;
  loop: boolean;
  onFrame?: (index: number, img: HTMLImageElement) => void;
}

export function useFrameTransport(frames: string[], opts: FrameTransportOptions): FrameTransport {
  const { fps, loop, onFrame } = opts;

  const [loadedCount, setLoadedCount] = useState(0);
  const [index, setIndex] = useState(0);
  const [playing, setPlaying] = useState(false);

  const indexRef = useRef(0);
  const imagesRef = useRef<HTMLImageElement[]>([]);
  const fpsRef = useRef(fps);
  const loopRef = useRef(loop);
  const onFrameRef = useRef(onFrame);

  // Reset the transport state when the frame list changes. This is the
  // documented "adjust state during render" pattern (React: You Might Not Need
  // an Effect) — the previous value lives in state, not a ref, so nothing reads
  // a ref during render, and it avoids the cascading re-render an effect would.
  const [prevFrames, setPrevFrames] = useState(frames);
  if (prevFrames !== frames) {
    setPrevFrames(frames);
    setLoadedCount(0);
    setIndex(0);
    setPlaying(false);
  }

  useEffect(() => {
    fpsRef.current = fps;
  }, [fps]);
  useEffect(() => {
    loopRef.current = loop;
  }, [loop]);
  useEffect(() => {
    onFrameRef.current = onFrame;
  }, [onFrame]);

  // Preload the whole sequence whenever the URL list changes. Errors count as
  // "loaded" too so a single bad frame can't wedge the preload gate forever.
  useEffect(() => {
    indexRef.current = 0;
    if (frames.length === 0) {
      imagesRef.current = [];
      return;
    }
    let cancelled = false;
    const bump = () => {
      if (!cancelled) setLoadedCount((n) => n + 1);
    };
    imagesRef.current = frames.map((url) => {
      const img = new Image();
      img.onload = bump;
      img.onerror = bump;
      img.src = url;
      return img;
    });
    return () => {
      cancelled = true;
    };
  }, [frames]);

  const ready = preloadComplete(loadedCount, frames.length);

  const show = useCallback((i: number) => {
    indexRef.current = i;
    setIndex(i);
    const img = imagesRef.current[i];
    if (img) onFrameRef.current?.(i, img);
  }, []);

  // Paint the poster (frame 0) once loaded — a pure external draw (index is
  // already 0 from the reset above, so no React state changes here).
  useEffect(() => {
    if (ready && imagesRef.current[0]) onFrameRef.current?.(0, imagesRef.current[0]);
  }, [ready]);

  useEffect(() => {
    if (!playing) return;
    let raf = 0;
    let last = performance.now();
    let acc = 0;
    let stopped = false;

    const tick = (now: number) => {
      acc += now - last;
      last = now;
      const steps = framesElapsed(acc, fpsRef.current);
      if (steps > 0) {
        acc -= steps * frameDurationMs(fpsRef.current);
        let i = indexRef.current;
        for (let s = 0; s < steps; s++) {
          const next = advanceIndex(i, imagesRef.current.length, loopRef.current);
          if (next === i && !loopRef.current) {
            stopped = true;
            break;
          }
          i = next;
        }
        show(i);
        if (stopped) {
          setPlaying(false);
          return;
        }
      }
      raf = requestAnimationFrame(tick);
    };
    raf = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(raf);
  }, [playing, show]);

  const play = useCallback(() => {
    if (ready) setPlaying(true);
  }, [ready]);
  const pause = useCallback(() => setPlaying(false), []);
  const toggle = useCallback(() => setPlaying((p) => (p ? false : ready)), [ready]);
  const seek = useCallback(
    (i: number) => {
      setPlaying(false);
      show(i);
    },
    [show],
  );

  return {
    ready,
    loadedCount,
    total: frames.length,
    index,
    playing,
    play,
    pause,
    toggle,
    seek,
  };
}
