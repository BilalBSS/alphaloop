# / strong refs for fire-and-forget background tasks

from __future__ import annotations

import asyncio
from collections.abc import Awaitable

import structlog

logger = structlog.get_logger(__name__)


class ExecutorTaskTracker:
    # / retains refs to spawned coroutines so they aren't gc'd mid-run

    def __init__(self, name: str = "executor") -> None:
        self._tasks: set[asyncio.Task] = set()
        self._name = name

    def spawn(self, coro: Awaitable) -> asyncio.Task | None:
        # / schedule coro, retain ref, auto-discard on completion
        try:
            task = asyncio.create_task(coro)
        except RuntimeError:
            # / no running loop
            return None
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    async def drain(self, timeout: float | None = None) -> None:
        # / await all in-flight tasks; called at shutdown
        if not self._tasks:
            return
        pending = list(self._tasks)
        try:
            await asyncio.wait_for(
                asyncio.gather(*pending, return_exceptions=True),
                timeout=timeout,
            )
        except asyncio.TimeoutError:
            logger.warning(
                "task_tracker_drain_timeout",
                name=self._name, pending=len(self._tasks),
            )

    def __len__(self) -> int:
        return len(self._tasks)
