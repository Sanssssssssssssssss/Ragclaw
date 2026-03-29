from __future__ import annotations

import json
import os
import urllib.error
import urllib.request


API_BASE = os.getenv("VERIFY_API_BASE", "http://127.0.0.1:8015/api").rstrip("/")

CASES = [
    {
        "label": "knowledge-positive-docs",
        "message": "\u6839\u636e\u77e5\u8bc6\u5e93\u603b\u7ed3\u4e00\u4e0b XSS\uff0c\u5e76\u5e26\u4e0a\u4f60\u5f15\u7528\u7684\u6587\u4ef6\u8def\u5f84\u3002",
        "expected": "knowledge",
    },
    {
        "label": "knowledge-positive-manual",
        "message": "\u6587\u6863\u91cc\u6709\u6ca1\u6709\u5173\u4e8e CSRF \u7684\u5185\u5bb9\uff1f\u8bf7\u6309\u6765\u6e90\u8def\u5f84\u56de\u7b54\u3002",
        "expected": "knowledge",
    },
    {
        "label": "chat-negative-debug",
        "message": "\u524d\u7aef\u6eda\u52a8\u7684\u65f6\u5019\u9875\u9762\u4e00\u76f4\u6296\uff0c\u5e2e\u6211\u4fee\u4e00\u4e0b\u8fd9\u4e2a UI bug\u3002",
        "expected": "chat",
    },
    {
        "label": "chat-negative-codebase",
        "message": "\u8bfb\u53d6 backend/config.py\uff0c\u544a\u8bc9\u6211 router model \u73b0\u5728\u600e\u4e48\u914d\u7f6e\u7684\u3002",
        "expected": "chat",
    },
    {
        "label": "chat-negative-general",
        "message": "\u8fd9\u4e2a\u9879\u76ee\u73b0\u5728\u7684\u67b6\u6784\u5927\u6982\u662f\u4ec0\u4e48\uff1f",
        "expected": "chat",
    },
]


def _request_json(path: str, payload: dict[str, object] | None = None) -> dict[str, object]:
    """Returns one parsed JSON object from path and payload inputs and performs a JSON HTTP request."""

    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}{path}",
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST" if payload is not None else "GET",
    )
    with urllib.request.urlopen(request, timeout=180) as response:
        return json.loads(response.read().decode("utf-8"))


def _stream_chat(session_id: str, message: str) -> list[tuple[str, dict[str, object]]]:
    """Returns one event list from session id and message inputs and collects the full SSE chat transcript."""

    payload = json.dumps(
        {
            "message": message,
            "session_id": session_id,
            "stream": True,
        }
    ).encode("utf-8")
    request = urllib.request.Request(
        f"{API_BASE}/chat",
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    with urllib.request.urlopen(request, timeout=180) as response:
        raw_text = response.read().decode("utf-8")

    events: list[tuple[str, dict[str, object]]] = []
    for block in raw_text.split("\n\n"):
        if not block.strip():
            continue
        event = "message"
        data_lines: list[str] = []
        for line in block.splitlines():
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
        if data_lines:
            events.append((event, json.loads("\n".join(data_lines))))
    return events


def main() -> int:
    """Returns one process exit code from no explicit inputs and verifies routing behavior through the live API."""

    passed = 0

    for case in CASES:
        try:
            session = _request_json("/sessions", {"title": f"routing-{case['label']}"})
            events = _stream_chat(str(session["id"]), case["message"])
        except urllib.error.URLError as exc:
            print(f"[ERROR] {case['label']}: failed to reach {API_BASE} ({exc})")
            return 1

        route = "chat"
        reason = ""
        for event_name, data in events:
            if (
                event_name == "retrieval"
                and str(data.get("stage", "")) == "routing"
                and str(data.get("title", "")) == "Knowledge route selected"
            ):
                route = "knowledge"
                reason = str(data.get("message", "")).strip()
                break

        ok = route == case["expected"]
        if ok:
            passed += 1
        status = "PASS" if ok else "FAIL"
        print(
            f"[{status}] {case['label']}: expected={case['expected']} actual={route} "
            f"reason={reason or 'no routing event emitted'}"
        )

    print(f"\nSummary: {passed}/{len(CASES)} cases matched expected routes.")
    return 0 if passed == len(CASES) else 1


if __name__ == "__main__":
    raise SystemExit(main())
