from __future__ import annotations

import asyncio
import multiprocessing
import sys
import threading
import time
import unittest
from pathlib import Path

import fakeredis
from redis.asyncio import Redis

BACKEND_DIR = Path(__file__).resolve().parents[1]
PROJECT_ROOT = BACKEND_DIR.parent
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.backend.runtime.policy import QueueLeaseLostError
from src.backend.runtime.redis_queue_backend import RedisLeaseSettings, RedisQueueBackend


def _queue_worker(redis_url: str, hold_seconds: float, event_queue) -> None:
    async def _run() -> None:
        backend = RedisQueueBackend.from_url(
            redis_url,
            settings=RedisLeaseSettings(
                namespace="ragclaw-test-process",
                lease_ttl_seconds=1.5,
                heartbeat_interval_seconds=0.5,
                poll_interval_seconds=0.02,
            ),
            now_factory=lambda: "2026-04-11T20:00:00Z",
        )
        lease = await backend.acquire("session-process", owner_id=f"worker-{multiprocessing.current_process().pid}")
        try:
            if lease.queued:
                await lease.wait_until_active(lambda: "2026-04-11T20:00:01Z")
            event_queue.put(("start", multiprocessing.current_process().pid, time.time()))
            await asyncio.sleep(hold_seconds)
            event_queue.put(("end", multiprocessing.current_process().pid, time.time()))
        finally:
            await backend.release(lease)
            await backend.close()

    asyncio.run(_run())


class RedisQueueBackendTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.server = fakeredis.TcpFakeServer(("127.0.0.1", 0), server_type="redis")
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()
        self.redis_url = f"redis://127.0.0.1:{self.server.server_address[1]}/0"

    def tearDown(self) -> None:
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    async def asyncSetUp(self) -> None:
        self.client = Redis.from_url(self.redis_url, decode_responses=True, protocol=3)
        self.backend = RedisQueueBackend(
            self.client,
            settings=RedisLeaseSettings(
                namespace="ragclaw-test",
                lease_ttl_seconds=0.4,
                heartbeat_interval_seconds=0.1,
                poll_interval_seconds=0.02,
            ),
            now_factory=lambda: "2026-04-11T20:00:00Z",
        )

    async def asyncTearDown(self) -> None:
        await self.backend.close()

    async def test_same_session_fifo_waiter_promotes_on_release(self) -> None:
        backend_two = RedisQueueBackend.from_url(
            self.redis_url,
            settings=RedisLeaseSettings(
                namespace="ragclaw-test",
                lease_ttl_seconds=0.4,
                heartbeat_interval_seconds=0.1,
                poll_interval_seconds=0.02,
            ),
            now_factory=lambda: "2026-04-11T20:00:00Z",
        )
        self.addAsyncCleanup(backend_two.close)

        first = await self.backend.acquire("session-1", owner_id="run-1")
        second = await backend_two.acquire("session-1", owner_id="run-2")

        self.assertFalse(first.queued)
        self.assertTrue(second.queued)

        activation_task = asyncio.create_task(second.wait_until_active(lambda: "2026-04-11T20:00:01Z"))
        await asyncio.sleep(0.05)
        self.assertFalse(activation_task.done())

        await self.backend.release(first)
        await activation_task

        self.assertTrue(await backend_two.is_active(second))
        self.assertEqual(second.dequeued_at, "2026-04-11T20:00:00Z")
        self.assertIsNotNone(second.fencing_token)

    async def test_heartbeat_renews_lease_and_expiry_is_detected(self) -> None:
        lease = await self.backend.acquire("session-ttl", owner_id="run-ttl")
        self.assertFalse(lease.queued)
        await asyncio.sleep(0.15)
        await lease.heartbeat()
        await asyncio.sleep(0.15)
        self.assertTrue(await self.backend.is_active(lease))
        await asyncio.sleep(0.45)
        self.assertFalse(await self.backend.is_active(lease))
        with self.assertRaises(QueueLeaseLostError):
            await lease.ensure_active()

    async def test_waiter_promotes_after_active_lease_expires(self) -> None:
        backend_two = RedisQueueBackend.from_url(
            self.redis_url,
            settings=RedisLeaseSettings(
                namespace="ragclaw-test-expiry",
                lease_ttl_seconds=0.15,
                heartbeat_interval_seconds=0.05,
                poll_interval_seconds=0.02,
            ),
            now_factory=lambda: "2026-04-11T20:00:00Z",
        )
        backend_one = RedisQueueBackend.from_url(
            self.redis_url,
            settings=RedisLeaseSettings(
                namespace="ragclaw-test-expiry",
                lease_ttl_seconds=0.15,
                heartbeat_interval_seconds=0.05,
                poll_interval_seconds=0.02,
            ),
            now_factory=lambda: "2026-04-11T20:00:00Z",
        )
        self.addAsyncCleanup(backend_one.close)
        self.addAsyncCleanup(backend_two.close)

        first = await backend_one.acquire("session-expiry", owner_id="run-1")
        second = await backend_two.acquire("session-expiry", owner_id="run-2")
        self.assertTrue(second.queued)

        await asyncio.sleep(0.2)
        await second.wait_until_active(lambda: "2026-04-11T20:00:01Z")
        self.assertTrue(await backend_two.is_active(second))
        self.assertFalse(await backend_one.is_active(first))

    async def test_release_is_idempotent(self) -> None:
        first = await self.backend.acquire("session-release", owner_id="run-1")
        second = await self.backend.acquire("session-release", owner_id="run-2")
        await self.backend.release(first)
        await self.backend.release(first)
        await second.wait_until_active(lambda: "2026-04-11T20:00:01Z")
        self.assertTrue(await self.backend.is_active(second))
        await self.backend.release(second)
        await self.backend.release(second)

    def test_two_process_workers_do_not_overlap_for_same_session(self) -> None:
        ctx = multiprocessing.get_context("spawn")
        event_queue = ctx.Queue()

        worker_one = ctx.Process(target=_queue_worker, args=(self.redis_url, 0.25, event_queue))
        worker_two = ctx.Process(target=_queue_worker, args=(self.redis_url, 0.25, event_queue))

        start = time.time()
        worker_one.start()
        time.sleep(0.05)
        worker_two.start()
        worker_one.join(timeout=10)
        worker_two.join(timeout=10)

        self.assertEqual(worker_one.exitcode, 0)
        self.assertEqual(worker_two.exitcode, 0)

        events: list[tuple[str, int, float]] = []
        while not event_queue.empty():
            events.append(event_queue.get())

        self.assertEqual(len(events), 4)

        windows: dict[int, dict[str, float]] = {}
        for kind, pid, ts in events:
            windows.setdefault(pid, {})[kind] = ts

        self.assertEqual(len(windows), 2)
        ordered = sorted((payload["start"], payload["end"]) for payload in windows.values())
        first_window, second_window = ordered
        self.assertLessEqual(first_window[1], second_window[0] + 0.02)
        self.assertLess(time.time() - start, 10)


if __name__ == "__main__":
    unittest.main()
