"""Lightweight runtime policy helpers, including per-session serial run queuing."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Callable


@dataclass
class QueueLease:
    session_id: str | None
    queued: bool
    queued_at: str | None = None
    dequeued_at: str | None = None
    _future: asyncio.Future[None] | None = field(default=None, repr=False)

    async def wait_until_active(self, now_factory: Callable[[], str]) -> None:
        if self._future is None:
            if self.dequeued_at is None:
                self.dequeued_at = now_factory()
            return
        await self._future
        if self.dequeued_at is None:
            self.dequeued_at = now_factory()


@dataclass
class _Waiter:
    future: asyncio.Future[None]
    queued_at: str


@dataclass
class _SessionState:
    active: bool = False
    waiters: deque[_Waiter] | None = None

    def __post_init__(self) -> None:
        if self.waiters is None:
            self.waiters = deque()


class SessionSerialQueue:
    """Provide FIFO serial execution per session_id."""

    def __init__(self, now_factory: Callable[[], str]) -> None:
        self._now_factory = now_factory
        self._manager_lock = asyncio.Lock()
        self._states: dict[str, _SessionState] = {}

    async def acquire(self, session_id: str | None) -> QueueLease:
        if not session_id:
            return QueueLease(session_id=None, queued=False)

        async with self._manager_lock:
            state = self._states.setdefault(session_id, _SessionState())
            if not state.active and not state.waiters:
                state.active = True
                return QueueLease(session_id=session_id, queued=False)

            waiter = _Waiter(
                future=asyncio.get_running_loop().create_future(),
                queued_at=self._now_factory(),
            )
            state.waiters.append(waiter)
            return QueueLease(
                session_id=session_id,
                queued=True,
                queued_at=waiter.queued_at,
                _future=waiter.future,
            )

    async def release(self, session_id: str | None) -> None:
        if not session_id:
            return

        next_waiter: _Waiter | None = None
        async with self._manager_lock:
            state = self._states.get(session_id)
            if state is None:
                return
            if state.waiters:
                next_waiter = state.waiters.popleft()
                state.active = True
            else:
                state.active = False
                self._states.pop(session_id, None)

        if next_waiter is not None and not next_waiter.future.done():
            next_waiter.future.set_result(None)
