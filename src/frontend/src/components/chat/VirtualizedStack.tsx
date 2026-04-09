"use client";

import {
  memo,
  useEffect,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type ReactNode,
  type RefObject
} from "react";

type VirtualizedStackProps<T> = {
  items: T[];
  getKey: (item: T, index: number) => string;
  renderItem: (item: T, index: number) => ReactNode;
  estimateHeight: number;
  overscan?: number;
  className?: string;
  containerRef?: RefObject<HTMLDivElement>;
  onTotalHeightChange?: (height: number) => void;
};

type LayoutEntry = {
  top: number;
  height: number;
};

const DEFAULT_OVERSCAN = 4;

function findStartIndex(layout: LayoutEntry[], scrollTop: number) {
  let low = 0;
  let high = layout.length - 1;
  let result = 0;

  while (low <= high) {
    const mid = Math.floor((low + high) / 2);
    if (layout[mid].top + layout[mid].height < scrollTop) {
      low = mid + 1;
    } else {
      result = mid;
      high = mid - 1;
    }
  }

  return result;
}

const MeasuredRow = memo(function MeasuredRow({
  itemKey,
  onHeightChange,
  top,
  children
}: {
  itemKey: string;
  onHeightChange: (itemKey: string, height: number) => void;
  top: number;
  children: ReactNode;
}) {
  const rowRef = useRef<HTMLDivElement | null>(null);

  useLayoutEffect(() => {
    const node = rowRef.current;
    if (!node) {
      return;
    }

    let frame: number | null = null;
    const reportHeight = () => {
      frame = null;
      onHeightChange(itemKey, node.offsetHeight);
    };

    reportHeight();

    const observer = new ResizeObserver(() => {
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
      }
      frame = window.requestAnimationFrame(reportHeight);
    });

    observer.observe(node);

    return () => {
      observer.disconnect();
      if (frame !== null) {
        window.cancelAnimationFrame(frame);
      }
    };
  }, [itemKey, onHeightChange]);

  return (
    <div className="absolute left-0 right-0" ref={rowRef} style={{ top }}>
      {children}
    </div>
  );
});

/**
 * Returns one virtualized scroll stack from item and renderer inputs and only mounts rows near the viewport.
 */
export function VirtualizedStack<T>({
  items,
  getKey,
  renderItem,
  estimateHeight,
  overscan = DEFAULT_OVERSCAN,
  className,
  containerRef,
  onTotalHeightChange
}: VirtualizedStackProps<T>) {
  const internalRef = useRef<HTMLDivElement | null>(null);
  const activeRef = (containerRef ?? internalRef) as RefObject<HTMLDivElement>;
  const frameRef = useRef<number | null>(null);
  const [scrollTop, setScrollTop] = useState(0);
  const [viewportHeight, setViewportHeight] = useState(0);
  const [measuredHeights, setMeasuredHeights] = useState<Record<string, number>>({});

  const layout = useMemo(() => {
    let cursor = 0;
    const entries = items.map((item, index) => {
      const key = getKey(item, index);
      const height = measuredHeights[key] ?? estimateHeight;
      const entry = {
        top: cursor,
        height
      };
      cursor += height;
      return entry;
    });

    return {
      entries,
      totalHeight: cursor
    };
  }, [estimateHeight, getKey, items, measuredHeights]);

  useEffect(() => {
    const container = activeRef.current;
    if (!container) {
      return;
    }

    const updateMetrics = () => {
      setScrollTop(container.scrollTop);
      setViewportHeight(container.clientHeight);
    };

    const onScroll = () => {
      if (frameRef.current !== null) {
        return;
      }

      frameRef.current = window.requestAnimationFrame(() => {
        frameRef.current = null;
        updateMetrics();
      });
    };

    updateMetrics();
    container.addEventListener("scroll", onScroll, { passive: true });
    window.addEventListener("resize", updateMetrics);

    return () => {
      container.removeEventListener("scroll", onScroll);
      window.removeEventListener("resize", updateMetrics);
      if (frameRef.current !== null) {
        window.cancelAnimationFrame(frameRef.current);
      }
    };
  }, [activeRef, items.length]);

  const onHeightChange = useMemo(
    () => (itemKey: string, height: number) => {
      const nextHeight = Math.max(height, 1);
      setMeasuredHeights((previous) =>
        previous[itemKey] === nextHeight ? previous : { ...previous, [itemKey]: nextHeight }
      );
    },
    []
  );

  const visibleRange = useMemo(() => {
    if (!items.length) {
      return {
        start: 0,
        end: -1
      };
    }

    const startIndex = Math.max(0, findStartIndex(layout.entries, scrollTop) - overscan);
    const bottomBoundary = scrollTop + viewportHeight;
    let endIndex = startIndex;

    while (
      endIndex < layout.entries.length &&
      layout.entries[endIndex].top < bottomBoundary + estimateHeight * overscan
    ) {
      endIndex += 1;
    }

    return {
      start: startIndex,
      end: Math.min(layout.entries.length - 1, endIndex)
    };
  }, [estimateHeight, items.length, layout.entries, overscan, scrollTop, viewportHeight]);

  const visibleItems = useMemo(() => {
    if (visibleRange.end < visibleRange.start) {
      return [];
    }

    return items.slice(visibleRange.start, visibleRange.end + 1).map((item, offset) => {
      const index = visibleRange.start + offset;
      return {
        index,
        item,
        key: getKey(item, index),
        top: layout.entries[index]?.top ?? 0
      };
    });
  }, [getKey, items, layout.entries, visibleRange.end, visibleRange.start]);

  useLayoutEffect(() => {
    onTotalHeightChange?.(layout.totalHeight);
  }, [layout.totalHeight, onTotalHeightChange]);

  return (
    <div className={className} ref={containerRef ?? internalRef}>
      <div style={{ height: layout.totalHeight, position: "relative" }}>
        {visibleItems.map(({ item, index, key, top }) => (
          <MeasuredRow itemKey={key} key={key} onHeightChange={onHeightChange} top={top}>
            {renderItem(item, index)}
          </MeasuredRow>
        ))}
      </div>
    </div>
  );
}
