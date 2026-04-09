from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

try:
    from .case_loader import BenchmarkSelection, load_cases
    from .evaluator import summarize_results
    from .runner import BenchmarkRunner
    from .storage_layout import rag_pdf_output_path
except ImportError:  # pragma: no cover - fallback for running inside backend cwd
    from benchmarks.case_loader import BenchmarkSelection, load_cases
    from benchmarks.evaluator import summarize_results
    from benchmarks.runner import BenchmarkRunner
    from benchmarks.storage_layout import rag_pdf_output_path


DEFAULT_BASE_URL = "http://127.0.0.1:8015"
DEFAULT_OUTPUT_PATH = rag_pdf_output_path()
INGESTION_ERRORS_PATH = BACKEND_DIR / "storage" / "knowledge" / "derived" / "ingestion_errors.json"
PDF_TARGETED_SLICES: tuple[tuple[str, str], ...] = (
    ("retrieval", "cross_file_aggregation"),
    ("grounding", "compare"),
    ("grounding", "multi_hop"),
    ("retrieval", "fuzzy"),
    ("retrieval", "compare"),
    ("grounding", "negation"),
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the focused PDF RAG benchmark slices against an already-running backend.")
    parser.add_argument("--base-url", default=DEFAULT_BASE_URL, help="Backend base URL, e.g. http://127.0.0.1:8015")
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT_PATH), help="Path for the JSON result file.")
    parser.add_argument("--case-delay-seconds", type=float, default=1.0, help="Sleep between benchmark cases.")
    parser.add_argument("--rebuild-index", action="store_true", help="Rebuild the knowledge index before running the targeted PDF slices.")
    parser.add_argument("--keep-sessions", action="store_true", help="Keep benchmark sessions instead of deleting them.")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    runner = BenchmarkRunner(
        args.base_url,
        selection=BenchmarkSelection(module="rag"),
        keep_sessions=args.keep_sessions,
        case_delay_seconds=args.case_delay_seconds,
    )
    try:
        runner.wait_for_health()
        if args.rebuild_index:
            index_status = runner.rebuild_knowledge_index()
        else:
            index_status = runner.get_knowledge_index_status()
        indexed_types = runner.indexed_source_types()
        manifest_stats = {}
        if INGESTION_ERRORS_PATH.exists():
            payload = json.loads(INGESTION_ERRORS_PATH.read_text(encoding="utf-8"))
            manifest_stats = payload.get("stats", {}) if isinstance(payload, dict) else {}

        results: list[dict] = []
        for rag_subtype, question_type in PDF_TARGETED_SLICES:
            selection = BenchmarkSelection(
                module="rag",
                rag_subtype=rag_subtype,
                question_type=question_type,
                modalities=("pdf",),
            )
            for case in load_cases(selection):
                results.append(runner.run_case(case, indexed_types))

        payload = {
            "started_at": datetime.now(timezone.utc).isoformat(),
            "base_url": args.base_url.rstrip("/"),
            "selection": {
                "preset": "pdf_targeted_focus",
                "modalities": "pdf",
                "slices": [
                    {"rag_subtype": rag_subtype, "question_type": question_type}
                    for rag_subtype, question_type in PDF_TARGETED_SLICES
                ],
            },
            "knowledge_index_status": index_status,
            "indexed_source_types": sorted(indexed_types),
            "knowledge_build_stats": manifest_stats,
            "summary": summarize_results(results),
            "cases": results,
        }

        output_path = Path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        output_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        print(output_path)
        print(json.dumps(payload["summary"], ensure_ascii=False, indent=2))
        return 0
    finally:
        runner.close()


if __name__ == "__main__":
    raise SystemExit(main())
