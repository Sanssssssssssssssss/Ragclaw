from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from src.backend.api.chat import router as chat_router
from src.backend.api.capabilities import router as capabilities_router
from src.backend.api.compress import router as compress_router
from src.backend.api.config_api import router as config_router
from src.backend.api.context import router as context_router
from src.backend.api.files import router as files_router
from src.backend.api.knowledge_index import router as knowledge_index_router
from src.backend.api.sessions import router as sessions_router
from src.backend.api.tokens import router as tokens_router
from src.backend.capabilities.skills_scanner import refresh_snapshot
from src.backend.knowledge import knowledge_indexer
from src.backend.knowledge.memory_indexer import memory_indexer
from src.backend.runtime.agent_manager import agent_manager
from src.backend.runtime.config import get_settings

logger = logging.getLogger(__name__)
_startup_tasks: set[asyncio.Task[Any]] = set()


async def _warm_knowledge_index() -> None:
    try:
        await asyncio.to_thread(knowledge_indexer.warm_start)
    except Exception:  # pragma: no cover - startup/runtime environment dependent
        logger.exception("Knowledge index warm start failed")


def _schedule_knowledge_warm_start() -> asyncio.Task[Any]:
    task: asyncio.Task[Any] = asyncio.create_task(_warm_knowledge_index(), name="knowledge-warm-start")
    _startup_tasks.add(task)

    def _cleanup(completed: asyncio.Task[Any]) -> None:
        _startup_tasks.discard(completed)
        try:
            completed.result()
        except asyncio.CancelledError:
            logger.info("Knowledge index warm start task cancelled during shutdown")
        except Exception:  # pragma: no cover - defensive logging
            logger.exception("Knowledge index warm start task failed")

    task.add_done_callback(_cleanup)
    return task


@asynccontextmanager
async def lifespan(_: FastAPI):
    settings = get_settings()
    refresh_snapshot(settings.backend_dir)
    agent_manager.initialize(settings.backend_dir)
    memory_indexer.configure(settings.backend_dir)
    memory_indexer.rebuild_index()
    knowledge_indexer.configure(settings.backend_dir)
    _schedule_knowledge_warm_start()
    try:
        yield
    finally:
        for task in list(_startup_tasks):
            if not task.done():
                task.cancel()


app = FastAPI(
    title="Mini-OpenClaw API",
    version="0.1.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(chat_router, prefix="/api", tags=["chat"])
app.include_router(capabilities_router, prefix="/api", tags=["capabilities"])
app.include_router(context_router, prefix="/api", tags=["context"])
app.include_router(sessions_router, prefix="/api", tags=["sessions"])
app.include_router(files_router, prefix="/api", tags=["files"])
app.include_router(tokens_router, prefix="/api", tags=["tokens"])
app.include_router(compress_router, prefix="/api", tags=["compress"])
app.include_router(config_router, prefix="/api", tags=["config"])
app.include_router(knowledge_index_router, prefix="/api", tags=["knowledge"])


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok"}
