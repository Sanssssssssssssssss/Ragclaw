from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from src.backend.runtime.config import get_settings


def _load_llama_index_components():
    """Returns the llama-index classes needed for memory indexing and lazy-loads heavy dependencies on demand."""

    from llama_index.core import (  # pylint: disable=import-outside-toplevel
        Document,
        Settings as LlamaSettings,
        StorageContext,
        VectorStoreIndex,
        load_index_from_storage,
    )
    from llama_index.core.node_parser import SentenceSplitter  # pylint: disable=import-outside-toplevel
    from llama_index.embeddings.openai import OpenAIEmbedding  # pylint: disable=import-outside-toplevel

    return Document, LlamaSettings, StorageContext, VectorStoreIndex, load_index_from_storage, SentenceSplitter, OpenAIEmbedding


class MemoryIndexer:
    """Returns memory retrieval snippets from the long-term memory file and manages the optional vector index."""

    def __init__(self) -> None:
        """Returns no value from no inputs and initializes the in-memory indexer state."""

        self.base_dir: Path | None = None
        self._index: Any | None = None

    def configure(self, base_dir: Path) -> None:
        """Returns no value from a base directory path input and prepares memory storage paths."""

        self.base_dir = base_dir
        self._memory_path.parent.mkdir(parents=True, exist_ok=True)
        self._storage_dir.mkdir(parents=True, exist_ok=True)

    @property
    def _memory_path(self) -> Path:
        """Returns the memory file path from no inputs and resolves the current long-term memory location."""

        if self.base_dir is None:
            raise RuntimeError("MemoryIndexer is not configured")
        return self.base_dir / "memory" / "MEMORY.md"

    @property
    def _storage_dir(self) -> Path:
        """Returns the storage directory path from no inputs and resolves the memory index persistence folder."""

        if self.base_dir is None:
            raise RuntimeError("MemoryIndexer is not configured")
        return self.base_dir / "storage" / "memory_index"

    @property
    def _meta_path(self) -> Path:
        """Returns the metadata file path from no inputs and points to the memory index manifest file."""

        return self._storage_dir / "meta.json"

    def _supports_embeddings(self) -> bool:
        """Returns a boolean from no inputs and checks whether memory embeddings are currently available."""

        return bool(get_settings().embedding_api_key)

    def _build_embed_model(self):
        """Returns an embedding model from no inputs and constructs the current memory embedding backend."""

        settings = get_settings()
        _, _, _, _, _, _, OpenAIEmbedding = _load_llama_index_components()
        return OpenAIEmbedding(
            api_key=settings.embedding_api_key,
            api_base=settings.embedding_base_url,
            model=settings.embedding_model,
        )

    def _file_digest(self) -> str:
        """Returns a digest string from no inputs and computes the current memory file checksum."""

        if not self._memory_path.exists():
            return ""
        return hashlib.md5(self._memory_path.read_bytes()).hexdigest()

    def _read_meta(self) -> dict[str, Any]:
        """Returns a metadata dictionary from no inputs and loads the persisted memory index manifest."""

        if not self._meta_path.exists():
            return {}
        try:
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            return {}

    def _write_meta(self, digest: str) -> None:
        """Returns no value from a digest string input and writes the latest memory index manifest."""

        self._meta_path.write_text(
            json.dumps({"digest": digest}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def rebuild_index(self) -> None:
        """Returns no value from no inputs and rebuilds the optional memory vector index from the memory file."""

        if self.base_dir is None:
            return

        if not self._memory_path.exists():
            self._memory_path.write_text("# Long-term Memory\n\n", encoding="utf-8")

        digest = self._file_digest()
        self._write_meta(digest)

        if not self._supports_embeddings():
            self._index = None
            return

        try:
            Document, LlamaSettings, _, VectorStoreIndex, _, SentenceSplitter, _ = _load_llama_index_components()
            LlamaSettings.embed_model = self._build_embed_model()
            content = self._memory_path.read_text(encoding="utf-8").strip()
            splitter = SentenceSplitter(chunk_size=256, chunk_overlap=32)
            documents = [Document(text=content, metadata={"source": "memory/MEMORY.md"})]
            nodes = splitter.get_nodes_from_documents(documents)
            self._index = VectorStoreIndex(nodes)
            self._index.storage_context.persist(persist_dir=str(self._storage_dir))
        except Exception:
            self._index = None

    def _load_index(self) -> None:
        """Returns no value from no inputs and restores the persisted memory vector index when available."""

        if not self._supports_embeddings():
            self._index = None
            return
        persisted_files = [
            path for path in self._storage_dir.iterdir() if path.name not in {".gitkeep", "meta.json"}
        ]
        if not persisted_files:
            self.rebuild_index()
            return
        try:
            _, LlamaSettings, StorageContext, _, load_index_from_storage, _, _ = _load_llama_index_components()
            LlamaSettings.embed_model = self._build_embed_model()
            storage_context = StorageContext.from_defaults(persist_dir=str(self._storage_dir))
            self._index = load_index_from_storage(storage_context)
        except Exception:
            self._index = None

    def _maybe_rebuild(self) -> None:
        """Returns no value from no inputs and refreshes the memory index when the source file has changed."""

        if self.base_dir is None:
            return
        digest = self._file_digest()
        if digest != self._read_meta().get("digest"):
            self.rebuild_index()
            return
        if self._index is None and self._supports_embeddings():
            self._load_index()

    def retrieve(self, query: str, top_k: int = 3) -> list[dict[str, Any]]:
        """Returns memory snippets from query and top-k inputs and retrieves the most relevant memory evidence."""

        if self.base_dir is None:
            return []

        self._maybe_rebuild()
        if self._index is None:
            return []

        retriever = self._index.as_retriever(similarity_top_k=top_k)
        results = retriever.retrieve(query)
        payload: list[dict[str, Any]] = []
        for item in results:
            node = getattr(item, "node", item)
            text = getattr(node, "text", "") or getattr(node, "get_content", lambda: "")()
            payload.append(
                {
                    "text": text,
                    "score": float(getattr(item, "score", 0.0) or 0.0),
                    "source": node.metadata.get("source", "memory/MEMORY.md"),
                }
            )
        return payload


memory_indexer = MemoryIndexer()
