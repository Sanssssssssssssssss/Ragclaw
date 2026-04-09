"use client";

import { useEffect, useMemo, useState } from "react";

type AnimatedLayer = {
  palette: Record<string, string>;
  frames: string[][];
};

type StaticLayer = {
  palette: Record<string, string>;
  grid: string[];
};

const PIXEL_SIZE = 8;
const SIZE = 16;

// Ghost Friend icon data adapted from Pxlkit's ghost-friend icon.
// Source: https://github.com/joangeldelarosa/pxlkit
// License: Pxlkit License v1.0 (attribution required)
const ghostShadow: AnimatedLayer = {
  palette: {
    S: "#2a2a2a",
    T: "#171717"
  },
  frames: [
    [
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "....SSSSSS......",
      "...SSSSSSSS.....",
      "....SSSSSS......"
    ],
    [
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      ".....TTTT.......",
      "....TTTTTT......",
      ".....TTTT......."
    ]
  ]
};

const ghostTrail: AnimatedLayer = {
  palette: {
    A: "#8f8ac6",
    B: "#6b67a6"
  },
  frames: [
    [
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "......A.........",
      ".....A.A........",
      "....A...A.......",
      "................",
      "................"
    ],
    [
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      ".....B..........",
      "....B.B.........",
      "...B...B........",
      "................",
      "................"
    ]
  ]
};

const ghostBody: AnimatedLayer = {
  palette: {
    W: "#fff8f2",
    L: "#ece8ff"
  },
  frames: [
    [
      "................",
      "................",
      "................",
      ".....WWWW.......",
      "....WWWWWW......",
      "...WWWWWWWW.....",
      "...WWWWWWWW.....",
      "...WWWWWWWW.....",
      "...WWWWWWWW.....",
      "...WWLWWLWW.....",
      "...WW.WW.WW.....",
      "................",
      "................",
      "................",
      "................",
      "................"
    ],
    [
      "................",
      "................",
      ".....WWWW.......",
      "....WWWWWW......",
      "...WWWWWWWW.....",
      "...WWWWWWWW.....",
      "...WWWWWWWW.....",
      "...WWWWWWWW.....",
      "...WWWWWWWW.....",
      "...WWLWWLWW.....",
      "...W.WW.W.W.....",
      "................",
      "................",
      "................",
      "................",
      "................"
    ]
  ]
};

const ghostFace: AnimatedLayer = {
  palette: {
    E: "#171717",
    M: "#535353"
  },
  frames: [
    [
      "................",
      "................",
      "................",
      "................",
      "................",
      "....EE..EE......",
      "....EE..EE......",
      "................",
      ".....MMMM.......",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................"
    ],
    [
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "....EE..EE......",
      "................",
      ".....MMMM.......",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................"
    ],
    [
      "................",
      "................",
      "................",
      "................",
      "................",
      "....EE..EE......",
      "....EE..EE......",
      "................",
      "......MM........",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................"
    ]
  ]
};

const ghostBlush: AnimatedLayer = {
  palette: {
    P: "#ff8c9c",
    Q: "#ffb0bc"
  },
  frames: [
    [
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "...PP....PP.....",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................"
    ],
    [
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "...QQ....QQ.....",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................",
      "................"
    ]
  ]
};

const joystickBadge: StaticLayer = {
  palette: {
    O: "#ff914d",
    W: "#fff8f2",
    D: "#1d1d1d"
  },
  grid: [
    "................",
    "................",
    "......OO........",
    "......OO........",
    "......OO........",
    "....WWWWWW......",
    "....WWDDWW......",
    "...WWWWWWWW.....",
    "...WWWWWWWW.....",
    "...WWOWWOWW.....",
    "....WOOOOO......",
    ".....WOOO.......",
    "................",
    "................",
    "................",
    "................"
  ]
};

function renderPixels(grid: string[], palette: Record<string, string>, keyPrefix: string) {
  const pixels = [];

  for (let y = 0; y < grid.length; y += 1) {
    for (let x = 0; x < grid[y].length; x += 1) {
      const cell = grid[y][x];
      if (cell === "." || !palette[cell]) {
        continue;
      }

      pixels.push(
        <rect
          fill={palette[cell]}
          height={PIXEL_SIZE}
          key={`${keyPrefix}-${x}-${y}`}
          shapeRendering="crispEdges"
          width={PIXEL_SIZE}
          x={x * PIXEL_SIZE}
          y={y * PIXEL_SIZE}
        />
      );
    }
  }

  return pixels;
}

export function PixelGhostFriend({
  className
}: {
  className?: string;
}) {
  const [frameIndex, setFrameIndex] = useState(0);

  useEffect(() => {
    const timer = window.setInterval(() => {
      setFrameIndex((value) => (value + 1) % 6);
    }, 360);

    return () => window.clearInterval(timer);
  }, []);

  const layers = useMemo(
    () => [
      renderPixels(
        ghostShadow.frames[frameIndex % ghostShadow.frames.length],
        ghostShadow.palette,
        "shadow"
      ),
      renderPixels(
        ghostTrail.frames[frameIndex % ghostTrail.frames.length],
        ghostTrail.palette,
        "trail"
      ),
      renderPixels(
        ghostBody.frames[frameIndex % ghostBody.frames.length],
        ghostBody.palette,
        "body"
      ),
      renderPixels(
        ghostFace.frames[frameIndex % ghostFace.frames.length],
        ghostFace.palette,
        "face"
      ),
      renderPixels(
        ghostBlush.frames[frameIndex % ghostBlush.frames.length],
        ghostBlush.palette,
        "blush"
      )
    ],
    [frameIndex]
  );

  return (
    <svg
      aria-label="Ghost Friend icon by Pxlkit"
      className={className}
      viewBox={`0 0 ${SIZE * PIXEL_SIZE} ${SIZE * PIXEL_SIZE}`}
    >
      {layers}
    </svg>
  );
}

export function PixelJoystickBadge({
  className
}: {
  className?: string;
}) {
  return (
    <svg
      aria-label="Pixel joystick accent"
      className={className}
      viewBox={`0 0 ${SIZE * PIXEL_SIZE} ${SIZE * PIXEL_SIZE}`}
    >
      {renderPixels(joystickBadge.grid, joystickBadge.palette, "joystick")}
    </svg>
  );
}
