from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from config import get_settings
from knowledge_retrieval import knowledge_indexer


def main() -> None:
    settings = get_settings()
    knowledge_indexer.configure(settings.backend_dir)
    knowledge_indexer.rebuild_index()

    status = knowledge_indexer.status().to_dict()
    vector_hits = [
        item.to_dict()
        for item in knowledge_indexer.retrieve_vector("CSRF security token", top_k=3)
    ]
    bm25_hits = [
        item.to_dict()
        for item in knowledge_indexer.retrieve_bm25("CSRF security token", top_k=3)
    ]

    print(
        json.dumps(
            {
                "embedding_provider": settings.embedding_provider,
                "embedding_model": settings.embedding_model,
                "status": status,
                "vector_hits": vector_hits,
                "bm25_hits": bm25_hits,
            },
            ensure_ascii=True,
        )
    )


if __name__ == "__main__":
    main()
