"use client";

import { useEffect, useState } from "react";

export function ResizeHandle({
  onResize
}: {
  onResize: (delta: number) => void;
}) {
  const [dragging, setDragging] = useState(false);

  useEffect(() => {
    if (!dragging) {
      return;
    }

    const onMouseMove = (event: MouseEvent) => {
      onResize(event.movementX);
    };
    const onMouseUp = () => {
      setDragging(false);
    };

    window.addEventListener("mousemove", onMouseMove);
    window.addEventListener("mouseup", onMouseUp);

    return () => {
      window.removeEventListener("mousemove", onMouseMove);
      window.removeEventListener("mouseup", onMouseUp);
    };
  }, [dragging, onResize]);

  return (
    <div
      aria-hidden
      className="group flex w-3 cursor-col-resize items-center justify-center"
      onMouseDown={() => setDragging(true)}
    >
      <div className="h-24 w-px rounded-full bg-[rgba(255,255,255,0.08)] transition-all duration-150 group-hover:h-32 group-hover:bg-[rgba(16,163,127,0.55)]" />
    </div>
  );
}
