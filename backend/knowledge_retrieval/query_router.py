from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any


HIGH_RECALL_PATTERNS = (
    re.compile(r"\bknowledge\b", re.IGNORECASE),
    re.compile(r"\bdocs?\b", re.IGNORECASE),
    re.compile(r"\bdocument(?:ation)?\b", re.IGNORECASE),
    re.compile(r"\bmanual\b", re.IGNORECASE),
    re.compile(r"\bpdf\b", re.IGNORECASE),
    re.compile(r"\bmarkdown\b", re.IGNORECASE),
    re.compile(r"\b(md|json|xlsx|xls|csv|txt)\b", re.IGNORECASE),
    re.compile("\u77e5\u8bc6\u5e93"),
    re.compile("\u6587\u6863"),
    re.compile("\u8d44\u6599"),
    re.compile("\u624b\u518c"),
    re.compile("\u767d\u76ae\u4e66"),
    re.compile(
        "\u6839\u636e.*(\u77e5\u8bc6\u5e93|\u6587\u6863|\u8d44\u6599)"
    ),
    re.compile(
        "(\u6587\u4ef6\u8def\u5f84|\u6765\u6e90|\u51fa\u5904|\u5f15\u7528\u8def\u5f84)"
    ),
)

@dataclass(frozen=True)
class RouteDecision:
    """Returns a route decision object from routing metadata fields and describes one routing outcome."""

    route: str
    reason: str
    classifier_used: bool
    regex_matched: bool

    @property
    def use_knowledge(self) -> bool:
        """Returns a boolean from no inputs and indicates whether the knowledge route should handle the query."""

        return self.route == "knowledge"


class KnowledgeQueryRouter:
    """Returns route decisions from user messages and applies a regex-only knowledge routing policy."""

    def _prefilter(self, message: str) -> bool:
        """Returns a boolean from one message string input and applies the high-recall regex prefilter."""

        return any(pattern.search(message) for pattern in HIGH_RECALL_PATTERNS)

    async def classify(
        self,
        message: str,
        history: list[dict[str, Any]],
        model_builder: Any,
    ) -> RouteDecision:
        """Returns a RouteDecision from message, history, and model-builder inputs and performs regex-only routing."""

        del history
        del model_builder

        regex_matched = self._prefilter(message)
        if not regex_matched:
            return RouteDecision(
                route="chat",
                reason="Regex route did not match knowledge or document signals.",
                classifier_used=False,
                regex_matched=False,
            )

        return RouteDecision(
            route="knowledge",
            reason="Regex route matched knowledge or document signals.",
            classifier_used=False,
            regex_matched=True,
        )


knowledge_query_router = KnowledgeQueryRouter()
