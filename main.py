#!/usr/bin/env python3
# / entry point — starts the agent orchestrator for paper trading

import asyncio
import signal
import sys

from dotenv import load_dotenv
load_dotenv()

import structlog

from src.agents.orchestrator import AgentOrchestrator

logger = structlog.get_logger(__name__)


async def run():
    mode = sys.argv[1] if len(sys.argv) > 1 else "paper"
    orchestrator = AgentOrchestrator(mode=mode)

    # / graceful shutdown on ctrl+c
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, lambda: asyncio.create_task(orchestrator.stop()))
        except NotImplementedError:
            # / windows doesn't support add_signal_handler
            pass

    try:
        await orchestrator.start()
    except KeyboardInterrupt:
        pass
    finally:
        await orchestrator.stop()


if __name__ == "__main__":
    try:
        asyncio.run(run())
    except KeyboardInterrupt:
        logger.info("shutdown_complete")
