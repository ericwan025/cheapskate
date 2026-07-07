"""SQS-backed job queue (Phase 3).

Presents the same interface the worker uses against Redis (reserve / complete /
requeue / requeue_orphans / pending_depth), so the worker code is backend-agnostic.

SQS provides the reliability the Redis pattern hand-rolled:
  - reserve()  -> ReceiveMessage. The message becomes *invisible* (not deleted)
    for the queue's visibility timeout. We remember its receipt handle.
  - complete() -> DeleteMessage. Only now does the job leave the system.
  - requeue()  -> ChangeMessageVisibility(0): make it visible again immediately
    (used on a spot interruption) instead of waiting out the timeout.
  - a worker that dies mid-job never deletes the message, so SQS re-delivers it
    when the visibility timeout lapses — no job lost. Hence requeue_orphans() is a
    no-op here (the broker recovers orphans for us).

Job payloads keep the same minimal shape as the rest of the system: {"id": <int>}.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

import boto3

from . import config

log = logging.getLogger("sqs")


class SqsQueue:
    def __init__(self, worker_id: str, queue_url: str = "", region: str = ""):
        self.worker_id = worker_id
        self.queue_url = queue_url or config.SQS_QUEUE_URL
        if not self.queue_url:
            raise RuntimeError("SQS_QUEUE_URL is required when QUEUE_BACKEND=sqs")
        region = region or config.AWS_REGION or None
        self.client = boto3.client("sqs", region_name=region)
        # Maps in-flight job id -> receipt handle, so complete()/requeue() can act
        # on the right message. A worker holds one job at a time.
        self._handles: dict[int, str] = {}

    # --- producer side ---------------------------------------------------
    def push(self, job: dict) -> None:
        self.client.send_message(QueueUrl=self.queue_url, MessageBody=json.dumps(job))

    def pending_depth(self) -> int:
        attrs = self.client.get_queue_attributes(
            QueueUrl=self.queue_url, AttributeNames=["ApproximateNumberOfMessages"]
        )["Attributes"]
        return int(attrs["ApproximateNumberOfMessages"])

    # --- worker side -----------------------------------------------------
    def reserve(self, block_seconds: int = config.SQS_WAIT_SECONDS) -> Optional[dict]:
        resp = self.client.receive_message(
            QueueUrl=self.queue_url,
            MaxNumberOfMessages=1,
            WaitTimeSeconds=min(block_seconds, 20),  # SQS caps long-poll at 20s
        )
        messages = resp.get("Messages", [])
        if not messages:
            return None
        msg = messages[0]
        job = json.loads(msg["Body"])
        self._handles[job["id"]] = msg["ReceiptHandle"]
        return job

    def complete(self, job: dict) -> None:
        handle = self._handles.pop(job["id"], None)
        if handle is None:
            log.warning("complete(%s) with no receipt handle — skipping", job.get("id"))
            return
        self.client.delete_message(QueueUrl=self.queue_url, ReceiptHandle=handle)

    def requeue(self, job: dict) -> None:
        # Make the message visible again right away instead of waiting out the
        # visibility timeout, so another worker can pick it up promptly.
        handle = self._handles.pop(job["id"], None)
        if handle is None:
            return
        try:
            self.client.change_message_visibility(
                QueueUrl=self.queue_url, ReceiptHandle=handle, VisibilityTimeout=0
            )
        except Exception as exc:  # noqa: BLE001 - if this fails, the timeout still redelivers
            log.warning("requeue(%s) visibility reset failed: %s", job.get("id"), exc)

    def requeue_orphans(self) -> int:
        # SQS redelivers messages whose visibility timeout lapses without a delete,
        # so there is nothing to recover on startup.
        return 0

    # --- introspection ---------------------------------------------------
    def completed_count(self) -> int:
        # SQS keeps no completed counter; completion == deletion. Not tracked.
        return 0

    def inflight_depth(self) -> int:
        attrs = self.client.get_queue_attributes(
            QueueUrl=self.queue_url,
            AttributeNames=["ApproximateNumberOfMessagesNotVisible"],
        )["Attributes"]
        return int(attrs["ApproximateNumberOfMessagesNotVisible"])
