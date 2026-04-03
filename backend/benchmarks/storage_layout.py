from __future__ import annotations

from pathlib import Path


BACKEND_DIR = Path(__file__).resolve().parents[1]
BENCHMARK_ROOT = BACKEND_DIR / "storage" / "benchmarks"


def benchmark_dir(*parts: str) -> Path:
    path = BENCHMARK_ROOT
    for part in parts:
        path = path / part
    return path


def harness_output_path(filename: str = "harness_benchmark_latest.json") -> Path:
    return benchmark_dir("harness") / filename


def harness_live_output_path(filename: str = "harness_live_validation_latest.json") -> Path:
    return benchmark_dir("harness", "live") / filename


def routing_output_path(filename: str = "routing_benchmark_latest.json") -> Path:
    return benchmark_dir("routing") / filename


def skill_gate_output_path(filename: str = "skill_gate_benchmark_latest.json") -> Path:
    return benchmark_dir("skill_gate") / filename


def rag_general_output_dir() -> Path:
    return benchmark_dir("rag", "general")


def rag_pdf_output_path(filename: str = "pdf_targeted_after_focus.json") -> Path:
    return benchmark_dir("rag", "pdf_targeted") / filename


def classify_benchmark_entry(path: Path) -> Path | None:
    name = path.name

    if path.is_dir():
        if name == "debug":
            return BENCHMARK_ROOT / "debug"
        return None

    if name.startswith("harness_benchmark"):
        return benchmark_dir("harness") / name
    if name.startswith("harness_live_validation"):
        return benchmark_dir("harness", "live") / name
    if name.startswith("routing_benchmark"):
        return benchmark_dir("routing") / name
    if name.startswith("skill_gate_benchmark"):
        return benchmark_dir("skill_gate") / name

    if name.startswith("pdf_targeted_"):
        return benchmark_dir("rag", "pdf_targeted") / name
    if name.startswith("baseline_formal_rag_"):
        return benchmark_dir("rag", "baselines") / name
    if name.startswith("grounding_fix_"):
        return benchmark_dir("rag", "grounding") / name
    if name.startswith("benchmark-results-") or name == "latest.json":
        return benchmark_dir("rag", "general") / name

    if name.endswith(".log") or name.endswith(".err.log") or name.endswith(".out.log"):
        return benchmark_dir("logs") / name

    return None
