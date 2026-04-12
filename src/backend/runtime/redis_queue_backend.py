from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import Callable
from uuid import uuid4

from redis.asyncio import Redis

from src.backend.runtime.policy import QueueLease, QueueLeaseLostError


@dataclass(frozen=True)
class RedisLeaseSettings:
    namespace: str = "ragclaw"
    lease_ttl_seconds: float = 30.0
    heartbeat_interval_seconds: float = 10.0
    poll_interval_seconds: float = 0.25
    mutation_lock_ttl_seconds: float = 5.0

    def __post_init__(self) -> None:
        if self.lease_ttl_seconds <= 0:
            raise ValueError("lease_ttl_seconds must be > 0")
        if self.heartbeat_interval_seconds <= 0:
            raise ValueError("heartbeat_interval_seconds must be > 0")
        if self.poll_interval_seconds <= 0:
            raise ValueError("poll_interval_seconds must be > 0")
        if self.mutation_lock_ttl_seconds <= 0:
            raise ValueError("mutation_lock_ttl_seconds must be > 0")
        if self.heartbeat_interval_seconds >= self.lease_ttl_seconds:
            raise ValueError("heartbeat_interval_seconds must be smaller than lease_ttl_seconds")

    @property
    def lease_ttl_ms(self) -> int:
        return max(1, int(self.lease_ttl_seconds * 1000))

    @property
    def mutation_lock_ttl_ms(self) -> int:
        return max(1, int(self.mutation_lock_ttl_seconds * 1000))


class RedisQueueBackend:
    """Redis-backed per-session FIFO queue with renewable active leases."""

    def __init__(
        self,
        client: Redis,
        *,
        settings: RedisLeaseSettings,
        now_factory: Callable[[], str],
    ) -> None:
        self._client = client
        self._settings = settings
        self._now_factory = now_factory

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        settings: RedisLeaseSettings,
        now_factory: Callable[[], str],
    ) -> "RedisQueueBackend":
        return cls(
            Redis.from_url(url, decode_responses=True, protocol=3),
            settings=settings,
            now_factory=now_factory,
        )

    def _session_prefix(self, session_id: str) -> str:
        return f"{self._settings.namespace}:queue:{session_id}"

    def _waiters_key(self, session_id: str) -> str:
        return f"{self._session_prefix(session_id)}:waiters"

    def _active_key(self, session_id: str) -> str:
        return f"{self._session_prefix(session_id)}:active"

    def _fence_key(self, session_id: str) -> str:
        return f"{self._session_prefix(session_id)}:fence"

    def _waiter_key(self, session_id: str, lease_id: str) -> str:
        return f"{self._session_prefix(session_id)}:waiter:{lease_id}"

    def _mutation_lock_key(self, session_id: str) -> str:
        return f"{self._session_prefix(session_id)}:mutation-lock"

    async def _acquire_mutation_lock(self, session_id: str) -> str:
        lock_key = self._mutation_lock_key(session_id)
        token = f"lock-{uuid4().hex}"
        while True:
            acquired = await self._client.set(
                lock_key,
                token,
                nx=True,
                px=self._settings.mutation_lock_ttl_ms,
            )
            if acquired:
                return token
            await asyncio.sleep(self._settings.poll_interval_seconds)

    async def _release_mutation_lock(self, session_id: str, token: str) -> None:
        lock_key = self._mutation_lock_key(session_id)
        current = await self._client.get(lock_key)
        if current == token:
            await self._client.delete(lock_key)

    async def _active_record(self, session_id: str) -> dict[str, str]:
        raw = await self._client.hgetall(self._active_key(session_id))
        return {str(key): str(value) for key, value in (raw or {}).items()}

    async def _promote_next_waiter_locked(self, session_id: str) -> tuple[str | None, int | None]:
        waiters_key = self._waiters_key(session_id)
        active_key = self._active_key(session_id)
        while True:
            head = await self._client.lindex(waiters_key, 0)
            if not head:
                return None, None
            lease_id = str(head)
            waiter_key = self._waiter_key(session_id, lease_id)
            waiter = await self._client.hgetall(waiter_key)
            await self._client.lpop(waiters_key)
            if not waiter:
                continue

            queued_at = str(waiter.get("queued_at") or "")
            owner_id = str(waiter.get("owner_id") or lease_id)
            fence = int(await self._client.incr(self._fence_key(session_id)))
            now = self._now_factory()
            await self._client.delete(waiter_key)
            await self._client.hset(
                active_key,
                mapping={
                    "lease_id": lease_id,
                    "owner_id": owner_id,
                    "queued_at": queued_at,
                    "dequeued_at": now,
                    "fencing_token": str(fence),
                    "heartbeat_at": now,
                },
            )
            await self._client.pexpire(active_key, self._settings.lease_ttl_ms)
            return now, fence

    async def acquire(self, session_id: str | None, *, owner_id: str | None = None) -> QueueLease:
        if not session_id:
            return QueueLease(session_id=None, queued=False, owner_id=owner_id)

        lease_id = f"lease-{uuid4().hex}"
        owner = str(owner_id or lease_id)
        queued_at = self._now_factory()
        lock_token = await self._acquire_mutation_lock(session_id)
        try:
            active = await self._active_record(session_id)
            waiters_key = self._waiters_key(session_id)
            if not active and await self._client.llen(waiters_key):
                await self._promote_next_waiter_locked(session_id)
                active = await self._active_record(session_id)

            if not active:
                fence = int(await self._client.incr(self._fence_key(session_id)))
                await self._client.hset(
                    self._active_key(session_id),
                    mapping={
                        "lease_id": lease_id,
                        "owner_id": owner,
                        "queued_at": "",
                        "dequeued_at": queued_at,
                        "fencing_token": str(fence),
                        "heartbeat_at": queued_at,
                    },
                )
                await self._client.pexpire(self._active_key(session_id), self._settings.lease_ttl_ms)
                return QueueLease(
                    session_id=session_id,
                    queued=False,
                    lease_id=lease_id,
                    owner_id=owner,
                    fencing_token=fence,
                    heartbeat_interval_seconds=self._settings.heartbeat_interval_seconds,
                    dequeued_at=queued_at,
                    _active_probe=lambda: self._is_active_lease(session_id, lease_id),
                    _heartbeat=lambda: self._heartbeat_lease(session_id, lease_id),
                )

            await self._client.hset(
                self._waiter_key(session_id, lease_id),
                mapping={
                    "lease_id": lease_id,
                    "owner_id": owner,
                    "queued_at": queued_at,
                },
            )
            await self._client.rpush(waiters_key, lease_id)
            return QueueLease(
                session_id=session_id,
                queued=True,
                lease_id=lease_id,
                owner_id=owner,
                queued_at=queued_at,
                heartbeat_interval_seconds=self._settings.heartbeat_interval_seconds,
                _activation_waiter=lambda: self._await_activation(session_id, lease_id),
                _active_probe=lambda: self._is_active_lease(session_id, lease_id),
                _heartbeat=lambda: self._heartbeat_lease(session_id, lease_id),
            )
        finally:
            await self._release_mutation_lock(session_id, lock_token)

    async def _await_activation(self, session_id: str, lease_id: str) -> tuple[str | None, int | None]:
        waiters_key = self._waiters_key(session_id)
        waiter_key = self._waiter_key(session_id, lease_id)
        while True:
            lock_token = await self._acquire_mutation_lock(session_id)
            try:
                active = await self._active_record(session_id)
                if active.get("lease_id") == lease_id:
                    fence_value = active.get("fencing_token")
                    return active.get("dequeued_at"), int(fence_value) if fence_value else None

                waiter_exists = bool(await self._client.exists(waiter_key))
                if not active:
                    head = await self._client.lindex(waiters_key, 0)
                    if head and str(head) == lease_id:
                        dequeued_at, fence = await self._promote_next_waiter_locked(session_id)
                        if dequeued_at is not None:
                            return dequeued_at, fence
                    elif head is None and waiter_exists:
                        await self._client.lpush(waiters_key, lease_id)
                        dequeued_at, fence = await self._promote_next_waiter_locked(session_id)
                        if dequeued_at is not None:
                            return dequeued_at, fence

                if not waiter_exists and not active:
                    raise QueueLeaseLostError(f"Queued lease lost for session_id={session_id} lease_id={lease_id}")
            finally:
                await self._release_mutation_lock(session_id, lock_token)

            await asyncio.sleep(self._settings.poll_interval_seconds)

    async def _is_active_lease(self, session_id: str, lease_id: str) -> bool:
        active = await self._active_record(session_id)
        return active.get("lease_id") == lease_id

    async def is_active(self, lease: QueueLease) -> bool:
        if not lease.session_id or not lease.lease_id:
            return True
        return await self._is_active_lease(lease.session_id, lease.lease_id)

    async def _heartbeat_lease(self, session_id: str, lease_id: str) -> bool:
        lock_token = await self._acquire_mutation_lock(session_id)
        try:
            active = await self._active_record(session_id)
            if active.get("lease_id") != lease_id:
                return False
            await self._client.hset(
                self._active_key(session_id),
                mapping={
                    "heartbeat_at": self._now_factory(),
                },
            )
            await self._client.pexpire(self._active_key(session_id), self._settings.lease_ttl_ms)
            return True
        finally:
            await self._release_mutation_lock(session_id, lock_token)

    async def heartbeat(self, lease: QueueLease) -> bool:
        if not lease.session_id or not lease.lease_id:
            return True
        return await self._heartbeat_lease(lease.session_id, lease.lease_id)

    async def release(self, lease_or_session: QueueLease | str | None) -> None:
        if isinstance(lease_or_session, QueueLease):
            session_id = lease_or_session.session_id
            lease_id = lease_or_session.lease_id
        else:
            session_id = str(lease_or_session or "").strip() or None
            lease_id = None
        if not session_id or not lease_id:
            return

        waiter_key = self._waiter_key(session_id, lease_id)
        lock_token = await self._acquire_mutation_lock(session_id)
        try:
            active = await self._active_record(session_id)
            if active.get("lease_id") == lease_id:
                await self._client.delete(self._active_key(session_id))
                await self._promote_next_waiter_locked(session_id)
                return
            await self._client.delete(waiter_key)
            await self._client.lrem(self._waiters_key(session_id), 0, lease_id)
        finally:
            await self._release_mutation_lock(session_id, lock_token)

    async def close(self) -> None:
        await self._client.aclose()


__all__ = ["RedisLeaseSettings", "RedisQueueBackend"]
