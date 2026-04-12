"""Lightweight runtime policy helpers, including per-session serial run queuing."""

from __future__ import annotations

import asyncio
from collections import deque
from dataclasses import dataclass, field
from typing import Awaitable, Callable
from uuid import uuid4


class QueueLeaseLostError(RuntimeError):
    """Raised when a queued or active lease is no longer owned by the current runner."""


@dataclass
class QueueLease:
    session_id: str | None
    queued: bool
    lease_id: str | None = None
    owner_id: str | None = None
    fencing_token: int | None = None
    heartbeat_interval_seconds: float | None = None
    queued_at: str | None = None
    dequeued_at: str | None = None
    _future: asyncio.Future[tuple[str | None, int | None]] | None = field(default=None, repr=False)
    _activation_waiter: Callable[[], Awaitable[tuple[str | None, int | None]]] | None = field(default=None, repr=False)
    _active_probe: Callable[[], Awaitable[bool]] | None = field(default=None, repr=False)
    _heartbeat: Callable[[], Awaitable[bool]] | None = field(default=None, repr=False)

    async def wait_until_active(self, now_factory: Callable[[], str]) -> None:
        activation_result: tuple[str | None, int | None] | None = None
        if self._future is not None:
            activation_result = await self._future
        elif self._activation_waiter is not None:
            activation_result = await self._activation_waiter()

        if activation_result is not None:
            dequeued_at, fencing_token = activation_result
            if dequeued_at and self.dequeued_at is None:
                self.dequeued_at = dequeued_at
            if fencing_token is not None:
                self.fencing_token = fencing_token
        if self.dequeued_at is None:
            self.dequeued_at = now_factory()

    async def ensure_active(self) -> None:
        if self._active_probe is None:
            return
        is_active = await self._active_probe()
        if not is_active:
            raise QueueLeaseLostError(
                f"Lease lost for session_id={self.session_id or '<none>'} lease_id={self.lease_id or '<none>'}"
            )

    async def heartbeat(self) -> None:
        if self._heartbeat is None:
            return
        renewed = await self._heartbeat()
        if not renewed:
            raise QueueLeaseLostError(
                f"Lease heartbeat failed for session_id={self.session_id or '<none>'} lease_id={self.lease_id or '<none>'}"
            )


@dataclass
class _Waiter:
    lease_id: str
    future: asyncio.Future[tuple[str | None, int | None]]
    queued_at: str


@dataclass
class _SessionState:
    active: bool = False
    active_lease_id: str | None = None
    waiters: deque[_Waiter] | None = None

    def __post_init__(self) -> None:
        if self.waiters is None:
            self.waiters = deque()


class InMemoryQueueBackend:
    """Provide FIFO serial execution per session_id for the default local mode."""

    def __init__(self, now_factory: Callable[[], str]) -> None:
        self._now_factory = now_factory
        self._manager_lock = asyncio.Lock()
        self._states: dict[str, _SessionState] = {}

    async def acquire(self, session_id: str | None, *, owner_id: str | None = None) -> QueueLease:
        if not session_id:
            return QueueLease(session_id=None, queued=False, owner_id=owner_id)

        lease_id = f"lease-{uuid4().hex}"

        async with self._manager_lock:
            state = self._states.setdefault(session_id, _SessionState())
            if not state.active and not state.waiters:
                state.active = True
                state.active_lease_id = lease_id
                return QueueLease(
                    session_id=session_id,
                    queued=False,
                    lease_id=lease_id,
                    owner_id=owner_id,
                    _active_probe=lambda: self._is_active(session_id, lease_id),
                    _heartbeat=lambda: self._is_active(session_id, lease_id),
                )

            waiter = _Waiter(
                lease_id=lease_id,
                future=asyncio.get_running_loop().create_future(),
                queued_at=self._now_factory(),
            )
            state.waiters.append(waiter)
            return QueueLease(
                session_id=session_id,
                queued=True,
                lease_id=lease_id,
                owner_id=owner_id,
                queued_at=waiter.queued_at,
                _future=waiter.future,
                _active_probe=lambda: self._is_active(session_id, lease_id),
                _heartbeat=lambda: self._is_active(session_id, lease_id),
            )

    async def _is_active(self, session_id: str, lease_id: str) -> bool:
        async with self._manager_lock:
            state = self._states.get(session_id)
            if state is None:
                return False
            return bool(state.active and state.active_lease_id == lease_id)

    async def is_active(self, lease: QueueLease) -> bool:
        if not lease.session_id or not lease.lease_id:
            return True
        return await self._is_active(lease.session_id, lease.lease_id)

    async def heartbeat(self, lease: QueueLease) -> bool:
        return await self.is_active(lease)

    async def release(self, lease_or_session: QueueLease | str | None) -> None:
        if isinstance(lease_or_session, QueueLease):
            session_id = lease_or_session.session_id
            lease_id = lease_or_session.lease_id
        else:
            session_id = lease_or_session
            lease_id = None
        if not session_id:
            return

        next_waiter: _Waiter | None = None
        async with self._manager_lock:
            state = self._states.get(session_id)
            if state is None:
                return
            if lease_id and state.active_lease_id != lease_id:
                for waiter in list(state.waiters or []):
                    if waiter.lease_id == lease_id:
                        state.waiters.remove(waiter)
                        if not waiter.future.done():
                            waiter.future.cancel()
                        break
                if not state.active and not state.waiters:
                    self._states.pop(session_id, None)
                return
            if state.waiters:
                next_waiter = state.waiters.popleft()
                state.active = True
                state.active_lease_id = next_waiter.lease_id
            else:
                state.active = False
                state.active_lease_id = None
                self._states.pop(session_id, None)

        if next_waiter is not None and not next_waiter.future.done():
            next_waiter.future.set_result((self._now_factory(), None))


class SessionSerialQueue(InMemoryQueueBackend):
    """Backward-compatible alias preserving the historical SessionSerialQueue name."""

    pass


__all__ = ["InMemoryQueueBackend", "QueueLease", "QueueLeaseLostError", "SessionSerialQueue"]
