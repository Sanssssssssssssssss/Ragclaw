"use client";

import { useEffect, useRef, useState } from "react";

export function ResizeHandle({
  onResize
}: {
  onResize: (delta: number) => void;
}) {
  const [dragging, setDragging] = useState(false);
  const frameRef = useRef<number | null>(null);
  const pendingDeltaRef = useRef(0);

  useEffect(() => {
    if (!dragging) {
      return;
    }

    const flush = () => {
      frameRef.current = null;
      if (!pendingDeltaRef.current) {
        return;
      }

      const delta = pendingDeltaRef.current;
      pendingDeltaRef.current = 0;
      onResize(delta);
    };

    const queueResize = (delta: number) => {
      pendingDeltaRef.current += delta;
      if (frameRef.current !== null) {
        return;
      }

      frameRef.current = window.requestAnimationFrame(flush);
    };

    const onPointerMove = (event: PointerEvent) => {
      queueResize(event.movementX);
    };
    const onPointerUp = () => {
      setDragging(false);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      if (pendingDeltaRef.current) {
        const delta = pendingDeltaRef.current;
        pendingDeltaRef.current = 0;
        onResize(delta);
      }
    };

    window.addEventListener("pointermove", onPointerMove);
    window.addEventListener("pointerup", onPointerUp);
    window.addEventListener("pointercancel", onPointerUp);

    return () => {
      window.removeEventListener("pointermove", onPointerMove);
      window.removeEventListener("pointerup", onPointerUp);
      window.removeEventListener("pointercancel", onPointerUp);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
        frameRef.current = null;
      }
      pendingDeltaRef.current = 0;
    };
  }, [dragging, onResize]);

  return (
    <div
      aria-hidden
      className="group flex w-3 cursor-col-resize items-center justify-center"
      onPointerDown={() => setDragging(true)}
    >
      <div className="h-24 w-px rounded-full bg-[rgba(255,255,255,0.08)] transition-all duration-150 group-hover:h-32 group-hover:bg-[rgba(16,163,127,0.55)]" />
    </div>
  );
}
