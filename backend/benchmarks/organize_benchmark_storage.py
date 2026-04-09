from __future__ import annotations

import argparse
import sys
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from benchmarks.storage_layout import BENCHMARK_ROOT, classify_benchmark_entry


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Organize benchmark outputs into category folders.")
    parser.add_argument("--root", default=str(BENCHMARK_ROOT), help="Benchmark storage root to organize.")
    parser.add_argument("--dry-run", action="store_true", help="Only print planned moves.")
    return parser.parse_args()


def organize_benchmark_storage(root: Path, *, dry_run: bool = False) -> list[tuple[Path, Path]]:
    root = root.resolve()
    planned: list[tuple[Path, Path]] = []
    for entry in root.iterdir():
        target = classify_benchmark_entry(entry)
        if target is None:
            continue
        target = root / target.relative_to(BENCHMARK_ROOT)
        if entry.resolve() == target:
            continue
        target.parent.mkdir(parents=True, exist_ok=True)
        planned.append((entry.resolve(), target))
        if not dry_run:
            if target.exists():
                target.unlink()
            entry.replace(target)
    return planned


def main() -> int:
    args = _parse_args()
    planned = organize_benchmark_storage(Path(args.root), dry_run=args.dry_run)
    for source, target in planned:
        print(f"{source} -> {target}")
    print(f"moved={0 if args.dry_run else len(planned)} planned={len(planned)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
