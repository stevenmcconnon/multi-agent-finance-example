"""Temporal worker on task queue `ingest`: `uv run python -m finance_agents.ingest.cli worker`.

In production: this worker runs as a Fargate service against Temporal Cloud (or a self-hosted cluster).
In this demo: one process against `temporal server start-dev`, because the sample needs no fleet.
"""

from __future__ import annotations

import asyncio

import structlog
from temporalio.client import Client
from temporalio.worker import Worker

from ..config import settings
from .workflow import ACTIVITIES, TASK_QUEUE, WORKFLOWS

log = structlog.get_logger(__name__)


async def connect() -> Client:
    return await Client.connect(settings.temporal_address, namespace=settings.temporal_namespace)


async def run() -> None:
    client = await connect()
    log.info("ingest.worker.start", address=settings.temporal_address, task_queue=TASK_QUEUE)
    async with Worker(client, task_queue=TASK_QUEUE, workflows=WORKFLOWS, activities=ACTIVITIES):
        await asyncio.Event().wait()
