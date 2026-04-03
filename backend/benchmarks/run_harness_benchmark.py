from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.harness_benchmark_lib import run_selected_benchmark
from benchmarks.storage_layout import harness_output_path


DEFAULT_OUTPUT_PATH = harness_output_path()


def _parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the layered harness benchmark suites.")
    parser.add_argument("--suite", choices=("contract", "integration", "hard", "scalable", "all"), default="contract")
    parser.add_argument("--case-file", action="append", default=[], help="Additional case file to load.")
    parser.add_argument("--tag", default=None, help="Only run cases containing one tag.")
    parser.add_argument("--limit", type=int, default=None, help="Limit loaded cases after filtering.")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="JSON output path.")
    return parser.parse_args(argv)


async def run_benchmark(
    output_path: Path,
    *,
    suite: str = "contract",
    case_files: list[str] | None = None,
    tag: str | None = None,
    limit: int | None = None,
) -> dict:
    return await run_selected_benchmark(
        suite=suite,
        extra_case_files=case_files,
        tag=tag,
        limit=limit,
        output_path=output_path,
    )


def main(argv: list[str] | None = None) -> int:
    args = _parse_args(argv)
    payload = asyncio.run(
        run_benchmark(
            Path(args.output),
            suite=args.suite,
            case_files=list(args.case_file or []),
            tag=args.tag,
            limit=args.limit,
        )
    )
    print(args.output)
    print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
