from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
from collections import Counter
from dataclasses import dataclass
from pathlib import Path
from typing import Any


TEXTUAL_ELEMENT_TYPES = {"paragraph", "heading", "list item", "caption"}
CONTAINER_TYPES = {"text block", "list"}
SKIP_CONTAINER_TYPES = {"header", "footer"}
MAX_TEXT_CHILD_CHARS = 1600
TEXT_GROUP_TARGET_CHARS = 900
TEXT_GROUP_MIN_CHARS = 260
TEXT_GROUP_MAX_CHARS = 1400
NEARBY_CONTEXT_CHARS = 220


@dataclass
class OpenDataLoaderPdfResult:
    documents: list[dict[str, Any]]
    errors: list[dict[str, str]]
    stats: dict[str, Any]


def _run_convert(input_paths: list[str], output_dir: Path) -> None:
    from opendataloader_pdf import convert  # pylint: disable=import-outside-toplevel

    convert(
        input_paths,
        output_dir=str(output_dir),
        format="json,markdown",
        quiet=True,
        use_struct_tree=True,
        image_output="external",
    )


def _safe_stage_name(index: int) -> str:
    return f"doc_{index:04d}"


def _slugify(value: str) -> str:
    normalized = re.sub(r"[^0-9a-zA-Z]+", "-", str(value or "").strip()).strip("-").lower()
    return normalized or "untitled"


def _relative_from(base_dir: Path, path: Path) -> str:
    return str(path.relative_to(base_dir)).replace("\\", "/")


def _bbox(value: Any) -> list[float] | None:
    if not isinstance(value, list) or len(value) != 4:
        return None
    try:
        return [float(item) for item in value]
    except (TypeError, ValueError):
        return None


def _page_number(node: dict[str, Any]) -> int | None:
    value = node.get("page number")
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _clean_text(value: Any) -> str:
    return " ".join(str(value or "").split()).strip()


def _looks_like_textual_content(node: dict[str, Any]) -> bool:
    return bool(_clean_text(node.get("content")))


def _section_title_text(value: str) -> str:
    cleaned = _clean_text(value)
    return cleaned[:240]


def _truncate(value: str, limit: int) -> str:
    cleaned = str(value or "").strip()
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: max(0, limit - 3)].rstrip() + "..."


def _trim_text_to_budget(text: str, budget: int) -> str:
    cleaned = str(text or "").strip()
    if budget <= 0:
        return ""
    if len(cleaned) <= budget:
        return cleaned
    return cleaned[: max(0, budget - 3)].rstrip() + "..."


def _iter_generic_children(node: dict[str, Any]) -> list[dict[str, Any]]:
    children: list[dict[str, Any]] = []
    for key in ("kids", "list items"):
        value = node.get(key)
        if isinstance(value, list):
            children.extend(child for child in value if isinstance(child, dict))
    return children


def _collect_node_index(root: dict[str, Any]) -> dict[int, dict[str, Any]]:
    index: dict[int, dict[str, Any]] = {}

    def walk(node: dict[str, Any]) -> None:
        raw_id = node.get("id")
        if isinstance(raw_id, int):
            index[raw_id] = node
        for child in _iter_generic_children(node):
            walk(child)
        if str(node.get("type", "")).strip().lower() == "table":
            for row in node.get("rows", []) or []:
                if not isinstance(row, dict):
                    continue
                for cell in row.get("cells", []) or []:
                    if isinstance(cell, dict):
                        walk(cell)

    walk(root)
    return index


def _cell_text(cell: dict[str, Any]) -> str:
    parts: list[str] = []

    def walk(node: dict[str, Any]) -> None:
        content = _clean_text(node.get("content"))
        if content:
            parts.append(content)
        for child in _iter_generic_children(node):
            walk(child)

    walk(cell)
    return " ".join(parts).strip()


def _table_payload(table_node: dict[str, Any]) -> tuple[list[dict[str, Any]], str]:
    structured_rows: list[dict[str, Any]] = []
    row_lines: list[str] = []

    for row in table_node.get("rows", []) or []:
        if not isinstance(row, dict):
            continue
        row_number = row.get("row number")
        cells_payload: list[dict[str, Any]] = []
        cell_texts: list[str] = []
        for cell in row.get("cells", []) or []:
            if not isinstance(cell, dict):
                continue
            text = _cell_text(cell)
            page = _page_number(cell)
            bbox = _bbox(cell.get("bounding box"))
            cell_payload = {
                "row_number": row.get("row number"),
                "column_number": cell.get("column number"),
                "row_span": cell.get("row span"),
                "column_span": cell.get("column span"),
                "page": page,
                "bbox": bbox,
                "text": text,
            }
            cells_payload.append(cell_payload)
            if text:
                column_number = cell.get("column number")
                cell_texts.append(f"c{column_number}: {text}")

        structured_rows.append(
            {
                "row_number": row_number,
                "cells": cells_payload,
            }
        )
        if cell_texts:
            row_lines.append(f"row {row_number}: " + " | ".join(cell_texts))

    return structured_rows, "\n".join(row_lines).strip()


def _merge_bboxes(boxes: list[list[float] | None]) -> list[float] | None:
    valid = [box for box in boxes if isinstance(box, list) and len(box) == 4]
    if not valid:
        return None
    return [
        min(box[0] for box in valid),
        min(box[1] for box in valid),
        max(box[2] for box in valid),
        max(box[3] for box in valid),
    ]


def _element_locator(
    *,
    page: int | None,
    element_type: str,
    section_title: str | None,
    element_id: Any,
) -> str:
    parts: list[str] = []
    if page is not None:
        parts.append(f"页 {page}")
    if section_title:
        parts.append(section_title)
    label = element_type
    if element_id is not None:
        label = f"{label} #{element_id}"
    parts.append(label)
    return " / ".join(part for part in parts if part)


def _split_oversized_text(
    *,
    text: str,
    limit: int,
) -> list[str]:
    cleaned = str(text or "").strip()
    if not cleaned:
        return []
    if len(cleaned) <= limit:
        return [cleaned]

    sentences = re.split(r"(?<=[。！？.!?])\s+|\n+", cleaned)
    chunks: list[str] = []
    current = ""
    for sentence in sentences:
        sentence = sentence.strip()
        if not sentence:
            continue
        candidate = f"{current} {sentence}".strip() if current else sentence
        if current and len(candidate) > limit:
            chunks.append(current)
            current = sentence
        else:
            current = candidate
    if current:
        chunks.append(current)

    normalized_chunks: list[str] = []
    for chunk in chunks or [cleaned]:
        if len(chunk) <= limit:
            normalized_chunks.append(chunk)
            continue
        normalized_chunks.extend(chunk[index : index + limit] for index in range(0, len(chunk), limit))
    return [item.strip() for item in normalized_chunks if item.strip()]


def _detect_struct_tree(pdf_path: Path) -> bool | None:
    try:
        from pypdf import PdfReader  # pylint: disable=import-outside-toplevel
    except ImportError:
        try:
            from PyPDF2 import PdfReader  # pylint: disable=import-outside-toplevel
        except ImportError:
            return None

    try:
        reader = PdfReader(str(pdf_path))
        root = reader.trailer.get("/Root", {})
        if hasattr(root, "get_object"):
            root = root.get_object()
        return "/StructTreeRoot" in root
    except Exception:
        return None


def _ensure_java_available() -> str:
    java_path = shutil.which("java")
    if java_path:
        return java_path

    java_home = os.getenv("JAVA_HOME", "").strip()
    if java_home:
        candidate = Path(java_home) / "bin" / ("java.exe" if os.name == "nt" else "java")
        if candidate.exists():
            os.environ["PATH"] = f"{candidate.parent}{os.pathsep}{os.environ.get('PATH', '')}"
            return str(candidate)

    if os.name == "nt":
        windows_roots = (
            Path(r"C:\Program Files\Java"),
            Path(r"C:\Program Files (x86)\Java"),
            Path(r"C:\Program Files\Eclipse Adoptium"),
            Path(r"C:\Program Files\Microsoft"),
        )
        seen: set[Path] = set()
        for root in windows_roots:
            if not root.exists():
                continue
            for candidate in sorted(root.rglob("java.exe"), reverse=True):
                resolved = candidate.resolve()
                if resolved in seen:
                    continue
                seen.add(resolved)
                os.environ["PATH"] = f"{candidate.parent}{os.pathsep}{os.environ.get('PATH', '')}"
                return str(candidate)

    raise RuntimeError("Java runtime not found. Install Java or set JAVA_HOME before rebuilding the knowledge index.")


def _preflight() -> None:
    try:
        from opendataloader_pdf import convert  # pylint: disable=import-outside-toplevel,unused-import
    except ImportError as exc:  # pragma: no cover - exercised in runtime builds
        raise RuntimeError("opendataloader-pdf is not installed. Run `pip install -U opendataloader-pdf`.") from exc

    java_path = _ensure_java_available()
    try:
        subprocess.run(
            [java_path, "-version"],
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception as exc:  # pragma: no cover - exercised in runtime builds
        raise RuntimeError(f"Java preflight failed: {exc}") from exc


def _move_derived_files(
    *,
    base_dir: Path,
    derived_root: Path,
    raw_output_dir: Path,
    stage_stem: str,
    source_relative: str,
) -> dict[str, str]:
    source_relative_path = Path(source_relative)
    final_dir = derived_root / source_relative_path.with_suffix("")
    final_dir.mkdir(parents=True, exist_ok=True)

    raw_json = raw_output_dir / f"{stage_stem}.json"
    raw_markdown = raw_output_dir / f"{stage_stem}.md"
    final_json = final_dir / "document.json"
    final_markdown = final_dir / "document.md"

    if raw_json.exists():
        if final_json.exists():
            final_json.unlink()
        shutil.move(str(raw_json), str(final_json))
    if raw_markdown.exists():
        if final_markdown.exists():
            final_markdown.unlink()
        shutil.move(str(raw_markdown), str(final_markdown))

    assets_dir = final_dir / "assets"
    for candidate in list(raw_output_dir.iterdir()):
        if candidate.name in {f"{stage_stem}.json", f"{stage_stem}.md"}:
            continue
        if not candidate.name.startswith(stage_stem):
            continue
        assets_dir.mkdir(parents=True, exist_ok=True)
        target_name = candidate.name[len(stage_stem) :].lstrip("-_") or candidate.name
        target_path = assets_dir / target_name
        if target_path.exists():
            if target_path.is_dir():
                shutil.rmtree(target_path)
            else:
                target_path.unlink()
        shutil.move(str(candidate), str(target_path))

    payload = {
        "derived_json_path": _relative_from(base_dir, final_json),
        "derived_markdown_path": _relative_from(base_dir, final_markdown),
    }
    if assets_dir.exists():
        payload["derived_assets_dir"] = _relative_from(base_dir, assets_dir)
    return payload


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _collect_semantic_items(
    *,
    source_relative: str,
    json_payload: dict[str, Any],
    node_index: dict[int, dict[str, Any]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []

    def walk_children(children: list[dict[str, Any]], current_section: str | None) -> str | None:
        section = current_section
        for child in children:
            section = walk_node(child, section)
        return section

    def walk_node(node: dict[str, Any], current_section: str | None) -> str | None:
        node_type = str(node.get("type", "")).strip().lower()
        node_id = node.get("id")
        page = _page_number(node)
        bbox = _bbox(node.get("bounding box"))
        section = current_section

        if node_type in SKIP_CONTAINER_TYPES:
            return current_section

        if node_type == "heading" and _looks_like_textual_content(node):
            section = _section_title_text(node.get("content", ""))

        if node_type in CONTAINER_TYPES:
            return walk_children(_iter_generic_children(node), section)

        if node_type == "table":
            structured_rows, table_text = _table_payload(node)
            items.append(
                {
                    "kind": "table",
                    "id": node_id,
                    "page": page,
                    "bbox": bbox,
                    "section_title": section,
                    "element_type": "table",
                    "text": table_text,
                    "table_rows": structured_rows,
                    "table_row_count": node.get("number of rows"),
                    "table_column_count": node.get("number of columns"),
                    "source_path": source_relative,
                }
            )
            return section

        if node_type == "image":
            items.append(
                {
                    "kind": "image",
                    "id": node_id,
                    "page": page,
                    "bbox": bbox,
                    "section_title": section,
                    "element_type": "image",
                    "image_source": node.get("source"),
                    "source_path": source_relative,
                }
            )
            return section

        if node_type in TEXTUAL_ELEMENT_TYPES and _looks_like_textual_content(node):
            linked_content_id = node.get("linked content id")
            linked_node = node_index.get(int(linked_content_id)) if isinstance(linked_content_id, int) else None
            items.append(
                {
                    "kind": "textual",
                    "id": node_id,
                    "page": page,
                    "bbox": bbox,
                    "section_title": section,
                    "element_type": node_type,
                    "text": _clean_text(node.get("content")),
                    "linked_content_id": linked_content_id,
                    "linked_content_type": str(linked_node.get("type", "")).strip().lower() if isinstance(linked_node, dict) else None,
                    "image_source": linked_node.get("source") if isinstance(linked_node, dict) else None,
                    "source_path": source_relative,
                }
            )
            return section

        children = _iter_generic_children(node)
        if children:
            return walk_children(children, section)
        return section

    root_children = [child for child in json_payload.get("kids", []) if isinstance(child, dict)]
    walk_children(root_children, None)
    return items


def _text_parent_id(source_relative: str, page: int | None, section_title: str | None, group_index: int) -> str:
    section_key = re.sub(r"[^0-9a-zA-Z\u4e00-\u9fff]+", "-", str(section_title or "section")).strip("-") or "section"
    return f"{source_relative}::text-parent::{page or 0}::{section_key}::{group_index}"


def _table_parent_id(source_relative: str, node_id: Any) -> str:
    return f"{source_relative}::table::{node_id or 'table'}"


def _figure_parent_id(source_relative: str, linked_content_id: Any, caption_id: Any) -> str:
    return f"{source_relative}::figure::{linked_content_id or caption_id or 'figure'}"


def _format_group_text(section_title: str | None, heading_text: str | None, body_items: list[dict[str, Any]]) -> str:
    lines: list[str] = []
    if heading_text:
        lines.append(heading_text)
    elif section_title:
        lines.append(section_title)
    for item in body_items:
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        if str(item.get("element_type", "")) == "list item" and not text.startswith(("-", "•")):
            text = f"- {text}"
        lines.append(text)
    return "\n".join(line for line in lines if line).strip()


def _nearest_context(items: list[dict[str, Any]], index: int, page: int | None, *, budget: int = NEARBY_CONTEXT_CHARS) -> str:
    snippets: list[str] = []
    remaining = budget
    for cursor in range(index - 1, max(-1, index - 3), -1):
        if cursor < 0:
            break
        candidate = items[cursor]
        if candidate.get("page") != page:
            continue
        if candidate.get("kind") != "textual":
            continue
        if str(candidate.get("element_type", "")) not in {"paragraph", "list item"}:
            continue
        text = _trim_text_to_budget(str(candidate.get("text", "") or ""), remaining)
        if text:
            snippets.insert(0, text)
            remaining -= len(text)
            if remaining <= 0:
                break
    return "\n".join(snippets).strip()


def _build_pdf_chunks(
    *,
    base_dir: Path,
    source_relative: str,
    json_payload: dict[str, Any],
    derived_paths: dict[str, str],
    has_struct_tree: bool | None,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    node_index = _collect_node_index(json_payload)
    chunks: list[dict[str, Any]] = []
    stats = Counter()
    citation_with_page = 0
    citation_with_bbox = 0
    chunk_lengths: list[int] = []
    text_chunk_lengths: list[int] = []
    parent_coverage_sizes: list[int] = []

    def append_chunk(item: dict[str, Any]) -> None:
        nonlocal citation_with_page, citation_with_bbox
        chunks.append(item)
        stats[f"{item['chunk_type']}_chunks"] += 1
        chunk_lengths.append(len(str(item.get("text", "") or "")))
        if item["chunk_type"] == "text":
            text_chunk_lengths.append(len(str(item.get("text", "") or "")))
        coverage_size = int(item.get("parent_coverage_size") or 0)
        if coverage_size:
            parent_coverage_sizes.append(coverage_size)
        if item.get("page") is not None:
            citation_with_page += 1
        if item.get("bbox") is not None:
            citation_with_bbox += 1

    def build_common_metadata(
        *,
        node: dict[str, Any],
        element_type: str,
        section_title: str | None,
        parent_id: str,
    ) -> dict[str, Any]:
        page = _page_number(node)
        bbox = _bbox(node.get("bounding box"))
        node_id = node.get("id")
        return {
            "source_path": source_relative,
            "source_type": "pdf",
            "file_type": "pdf",
            "chunk_type": "text",
            "locator": _element_locator(
                page=page,
                element_type=element_type,
                section_title=section_title,
                element_id=node_id,
            ),
            "parent_id": parent_id,
            "page": page,
            "bbox": bbox,
            "element_type": element_type,
            "section_title": section_title,
            "derived_json_path": derived_paths.get("derived_json_path"),
            "derived_markdown_path": derived_paths.get("derived_markdown_path"),
            "structure_mode": "struct_tree" if has_struct_tree else "heuristic",
        }

    semantic_items = _collect_semantic_items(
        source_relative=source_relative,
        json_payload=json_payload,
        node_index=node_index,
    )

    pending_heading: dict[str, Any] | None = None
    current_group: dict[str, Any] | None = None
    text_group_index = 0

    def flush_group() -> None:
        nonlocal current_group, text_group_index
        if not current_group or not current_group["body_items"]:
            current_group = None
            return

        text_group_index += 1
        parent_text = _format_group_text(
            current_group["section_title"],
            current_group["heading_text"],
            current_group["body_items"],
        )
        if not parent_text:
            current_group = None
            return

        parent_id = _text_parent_id(
            source_relative,
            current_group["page"],
            current_group["section_title"],
            text_group_index,
        )
        merged_bbox = _merge_bboxes(
            [current_group.get("heading_bbox")] + [item.get("bbox") for item in current_group["body_items"]]
        )
        element_types = sorted({str(item.get("element_type", "")) for item in current_group["body_items"] if item.get("element_type")})
        parent_coverage_size = len(current_group["body_items"]) + (1 if current_group.get("heading_text") else 0)
        slices = _split_oversized_text(text=parent_text, limit=MAX_TEXT_CHILD_CHARS)
        for index, slice_text in enumerate(slices, start=1):
            metadata = {
                "source_path": source_relative,
                "source_type": "pdf",
                "file_type": "pdf",
                "chunk_type": "text",
                "locator": _element_locator(
                    page=current_group["page"],
                    element_type="text-group",
                    section_title=current_group["section_title"],
                    element_id=text_group_index,
                ),
                "parent_id": parent_id,
                "page": current_group["page"],
                "bbox": merged_bbox,
                "element_type": "text-group",
                "section_title": current_group["section_title"],
                "derived_json_path": derived_paths.get("derived_json_path"),
                "derived_markdown_path": derived_paths.get("derived_markdown_path"),
                "structure_mode": "struct_tree" if has_struct_tree else "heuristic",
                "parent_coverage_size": parent_coverage_size,
                "merged_element_types": element_types,
            }
            append_chunk(
                {
                    "doc_id": f"{parent_id}::child::{index}",
                    "text": slice_text,
                    "parent_text": parent_text,
                    **metadata,
                }
            )
        current_group = None

    for index, item in enumerate(semantic_items):
        item_type = str(item.get("element_type", ""))
        item_page = item.get("page")
        item_section = item.get("section_title")

        if item_type == "heading":
            flush_group()
            pending_heading = item
            continue

        if item.get("kind") == "textual":
            linked_content_id = item.get("linked_content_id")
            if item_type == "caption" and linked_content_id is not None:
                continue

            item_text = str(item.get("text", "") or "").strip()
            if not item_text:
                continue

            should_start_new = current_group is None
            if current_group is not None:
                same_page = current_group["page"] == item_page
                same_section = current_group["section_title"] == item_section
                projected = current_group["body_chars"] + len(item_text)
                if not same_page or not same_section:
                    should_start_new = True
                elif projected > TEXT_GROUP_MAX_CHARS and current_group["body_chars"] >= TEXT_GROUP_MIN_CHARS:
                    should_start_new = True
                elif current_group["body_chars"] >= TEXT_GROUP_TARGET_CHARS and item_type == "paragraph":
                    should_start_new = True

            if should_start_new:
                flush_group()
                current_group = {
                    "page": item_page,
                    "section_title": item_section,
                    "heading_text": pending_heading.get("text") if pending_heading and pending_heading.get("page") == item_page and pending_heading.get("section_title") == item_section else None,
                    "heading_bbox": pending_heading.get("bbox") if pending_heading and pending_heading.get("page") == item_page and pending_heading.get("section_title") == item_section else None,
                    "body_items": [],
                    "body_chars": 0,
                }
                pending_heading = None

            current_group["body_items"].append(item)
            current_group["body_chars"] += len(item_text)
            continue

        flush_group()
        pending_heading = None

        if item.get("kind") == "table":
            section_title = item.get("section_title")
            nearby_context = _nearest_context(semantic_items, index, item_page)
            context_parts = []
            if section_title:
                context_parts.append(section_title)
            if nearby_context:
                context_parts.append(nearby_context)
            table_text = str(item.get("text", "") or "").strip()
            if not table_text:
                continue
            if context_parts:
                table_text = "\n".join(context_parts + [table_text])
            parent_id = _table_parent_id(source_relative, item.get("id"))
            locator = _element_locator(
                page=item_page,
                element_type="table",
                section_title=section_title,
                element_id=item.get("id"),
            )
            append_chunk(
                {
                    "doc_id": f"{parent_id}::child::1",
                    "text": table_text,
                    "parent_text": table_text,
                    "source_path": source_relative,
                    "source_type": "pdf",
                    "file_type": "pdf",
                    "chunk_type": "table",
                    "locator": locator,
                    "parent_id": parent_id,
                    "page": item_page,
                    "bbox": item.get("bbox"),
                    "element_type": "table",
                    "section_title": section_title,
                    "derived_json_path": derived_paths.get("derived_json_path"),
                    "derived_markdown_path": derived_paths.get("derived_markdown_path"),
                    "structure_mode": "struct_tree" if has_struct_tree else "heuristic",
                    "table_rows": item.get("table_rows"),
                    "table_row_count": item.get("table_row_count"),
                    "table_column_count": item.get("table_column_count"),
                    "parent_coverage_size": 1 + (1 if nearby_context else 0) + (1 if section_title else 0),
                }
            )
            continue

    flush_group()

    for item in semantic_items:
        if item.get("kind") != "textual" or str(item.get("element_type", "")) != "caption":
            continue
        if item.get("linked_content_id") is None:
            continue

        page = item.get("page")
        section_title = item.get("section_title")
        text = str(item.get("text", "") or "").strip()
        if not text:
            continue
        linked_node = node_index.get(int(item["linked_content_id"])) if isinstance(item.get("linked_content_id"), int) else None
        merged_bbox = _merge_bboxes([item.get("bbox"), _bbox(linked_node.get("bounding box")) if isinstance(linked_node, dict) else None])
        if section_title and section_title not in text:
            text = f"{section_title}\n{text}"
        parent_id = _figure_parent_id(source_relative, item.get("linked_content_id"), item.get("id"))
        append_chunk(
            {
                "doc_id": f"{parent_id}::child::1",
                "text": text,
                "parent_text": text,
                "source_path": source_relative,
                "source_type": "pdf",
                "file_type": "pdf",
                "chunk_type": "figure-caption",
                "locator": _element_locator(
                    page=page,
                    element_type="figure-caption",
                    section_title=section_title,
                    element_id=item.get("id"),
                ),
                "parent_id": parent_id,
                "page": page,
                "bbox": merged_bbox,
                "element_type": "caption",
                "section_title": section_title,
                "derived_json_path": derived_paths.get("derived_json_path"),
                "derived_markdown_path": derived_paths.get("derived_markdown_path"),
                "structure_mode": "struct_tree" if has_struct_tree else "heuristic",
                "linked_content_id": item.get("linked_content_id"),
                "linked_content_type": item.get("linked_content_type"),
                "image_source": item.get("image_source"),
                "parent_coverage_size": 2 if item.get("linked_content_id") is not None else 1,
            }
        )

    summary = {
        "chunk_counts": {
            "text": int(stats.get("text_chunks", 0)),
            "table": int(stats.get("table_chunks", 0)),
            "figure_caption": int(stats.get("figure-caption_chunks", 0)),
        },
        "avg_chunk_length": (sum(chunk_lengths) / len(chunk_lengths)) if chunk_lengths else 0.0,
        "avg_text_chunk_length": (sum(text_chunk_lengths) / len(text_chunk_lengths)) if text_chunk_lengths else 0.0,
        "avg_parent_coverage_size": (sum(parent_coverage_sizes) / len(parent_coverage_sizes)) if parent_coverage_sizes else 0.0,
        "citation_page_count": citation_with_page,
        "citation_bbox_count": citation_with_bbox,
        "total_chunks": len(chunks),
    }
    return chunks, summary


def build_pdf_documents_with_opendataloader(
    *,
    base_dir: Path,
    pdf_paths: list[Path],
    derived_root: Path,
) -> OpenDataLoaderPdfResult:
    if not pdf_paths:
        return OpenDataLoaderPdfResult(documents=[], errors=[], stats={})

    _preflight()

    batch_root = derived_root / "_batch"
    if batch_root.exists():
        shutil.rmtree(batch_root)
    staging_dir = batch_root / "staging"
    raw_output_dir = batch_root / "raw"
    staging_dir.mkdir(parents=True, exist_ok=True)
    raw_output_dir.mkdir(parents=True, exist_ok=True)

    staging_map: dict[str, dict[str, Any]] = {}
    input_paths: list[str] = []
    stats = {
        "backend": "opendataloader",
        "parsed_pdf_count": 0,
        "failed_pdf_count": 0,
        "failure_reasons": {},
        "chunk_counts": {"text": 0, "table": 0, "figure_caption": 0},
        "avg_chunk_length": 0.0,
        "avg_text_chunk_length": 0.0,
        "avg_parent_coverage_size": 0.0,
        "page_available_rate": 0.0,
        "bbox_available_rate": 0.0,
        "structure_modes": {"struct_tree": 0, "heuristic": 0, "unknown": 0},
    }
    all_chunk_lengths: list[int] = []
    all_text_chunk_lengths: list[int] = []
    all_parent_coverage_sizes: list[int] = []
    total_chunk_count = 0
    total_page_citations = 0
    total_bbox_citations = 0

    for index, pdf_path in enumerate(pdf_paths, start=1):
        stage_stem = _safe_stage_name(index)
        staged_pdf = staging_dir / f"{stage_stem}.pdf"
        shutil.copy2(pdf_path, staged_pdf)
        source_relative = _relative_from(base_dir, pdf_path)
        staging_map[stage_stem] = {
            "source_path": source_relative,
            "source_abs_path": pdf_path,
            "staged_pdf": staged_pdf,
            "has_struct_tree": _detect_struct_tree(staged_pdf),
        }
        input_paths.append(str(staged_pdf))

    isolated_failures: dict[str, str] = {}
    try:
        _run_convert(input_paths, raw_output_dir)
    except Exception as exc:
        if len(input_paths) == 1:
            raise

        # Keep batch as the normal path, but isolate failures with the same parser if one file poisons the batch.
        if raw_output_dir.exists():
            shutil.rmtree(raw_output_dir)
        raw_output_dir.mkdir(parents=True, exist_ok=True)
        for stage_stem, item in staging_map.items():
            try:
                _run_convert([str(item["staged_pdf"])], raw_output_dir)
            except Exception as item_exc:  # pragma: no cover - exercised in runtime builds
                isolated_failures[stage_stem] = str(item_exc).strip() or item_exc.__class__.__name__
        if not any((raw_output_dir / f"{stage_stem}.json").exists() for stage_stem in staging_map):
            raise exc

    documents: list[dict[str, Any]] = []
    errors: list[dict[str, str]] = []

    for stage_stem, item in staging_map.items():
        source_relative = str(item["source_path"])
        has_struct_tree = item["has_struct_tree"]
        raw_json = raw_output_dir / f"{stage_stem}.json"
        raw_markdown = raw_output_dir / f"{stage_stem}.md"
        if not raw_json.exists() or not raw_markdown.exists():
            reason = isolated_failures.get(stage_stem) or "Missing OpenDataLoader derived json/markdown output."
            errors.append({"source_path": source_relative, "stage": "convert", "message": reason})
            stats["failed_pdf_count"] += 1
            stats["failure_reasons"][reason] = int(stats["failure_reasons"].get(reason, 0)) + 1
            continue

        try:
            derived_paths = _move_derived_files(
                base_dir=base_dir,
                derived_root=derived_root,
                raw_output_dir=raw_output_dir,
                stage_stem=stage_stem,
                source_relative=source_relative,
            )
            json_payload = _load_json(base_dir / derived_paths["derived_json_path"])
            chunks, chunk_summary = _build_pdf_chunks(
                base_dir=base_dir,
                source_relative=source_relative,
                json_payload=json_payload,
                derived_paths=derived_paths,
                has_struct_tree=has_struct_tree,
            )
            if not chunks:
                reason = "OpenDataLoader produced no semantic chunks for the PDF."
                errors.append({"source_path": source_relative, "stage": "chunk", "message": reason})
                stats["failed_pdf_count"] += 1
                stats["failure_reasons"][reason] = int(stats["failure_reasons"].get(reason, 0)) + 1
                continue

            documents.extend(chunks)
            stats["parsed_pdf_count"] += 1
            structure_key = "unknown"
            if has_struct_tree is True:
                structure_key = "struct_tree"
            elif has_struct_tree is False:
                structure_key = "heuristic"
            stats["structure_modes"][structure_key] = int(stats["structure_modes"].get(structure_key, 0)) + 1
            stats["chunk_counts"]["text"] += int(chunk_summary["chunk_counts"]["text"])
            stats["chunk_counts"]["table"] += int(chunk_summary["chunk_counts"]["table"])
            stats["chunk_counts"]["figure_caption"] += int(chunk_summary["chunk_counts"]["figure_caption"])
            total_chunk_count += int(chunk_summary["total_chunks"])
            total_page_citations += int(chunk_summary["citation_page_count"])
            total_bbox_citations += int(chunk_summary["citation_bbox_count"])
            all_chunk_lengths.extend(len(str(chunk.get("text", "") or "")) for chunk in chunks)
            all_text_chunk_lengths.extend(
                len(str(chunk.get("text", "") or ""))
                for chunk in chunks
                if str(chunk.get("chunk_type", "")).strip() == "text"
            )
            all_parent_coverage_sizes.extend(
                int(chunk.get("parent_coverage_size") or 0)
                for chunk in chunks
                if int(chunk.get("parent_coverage_size") or 0) > 0
            )
        except Exception as exc:  # pragma: no cover - exercised in runtime builds
            message = str(exc).strip() or exc.__class__.__name__
            errors.append({"source_path": source_relative, "stage": "parse", "message": message})
            stats["failed_pdf_count"] += 1
            stats["failure_reasons"][message] = int(stats["failure_reasons"].get(message, 0)) + 1

    if total_chunk_count:
        stats["avg_chunk_length"] = sum(all_chunk_lengths) / len(all_chunk_lengths)
        stats["avg_text_chunk_length"] = (
            sum(all_text_chunk_lengths) / len(all_text_chunk_lengths)
            if all_text_chunk_lengths
            else 0.0
        )
        stats["avg_parent_coverage_size"] = (
            sum(all_parent_coverage_sizes) / len(all_parent_coverage_sizes)
            if all_parent_coverage_sizes
            else 0.0
        )
        stats["page_available_rate"] = total_page_citations / total_chunk_count
        stats["bbox_available_rate"] = total_bbox_citations / total_chunk_count

    return OpenDataLoaderPdfResult(documents=documents, errors=errors, stats=stats)
