"""Bounded synchronous child IPC. This module has no database capability."""

import json
from multiprocessing.connection import Connection

from a2_api.core.batches import MAX_CHECKPOINT_BYTES, BatchCheckpoint, BatchExecution

CHECKPOINT_PREFIX = b"OD1:"
MAX_RESUME_BYTES = 17 * 1024 * 1024


class CheckpointChannel:
    def __init__(self, connection: Connection, execution: BatchExecution) -> None:
        self.connection = connection
        self.execution = execution
        raw = connection.recv_bytes(MAX_RESUME_BYTES)
        payload = json.loads(raw)
        if payload["ipc_version"] != 1 or payload["execution_identity"] != execution.identity:
            raise ValueError("OD_RESUME_PROTOCOL_MISMATCH")
        self.completed = [BatchCheckpoint.model_validate(value) for value in payload["checkpoints"]]
        seen = set()
        for checkpoint in self.completed:
            if checkpoint.schema_version == "waydex-walk-checkpoint/2":
                checkpoint.validate_pair_inventory(execution)
            else:
                checkpoint.validate_inventory(execution)
            if checkpoint.batch_id in seen:
                raise ValueError("OD_DUPLICATE_RESUME_BATCH")
            seen.add(checkpoint.batch_id)

    def commit(self, checkpoint: BatchCheckpoint) -> None:
        if checkpoint.schema_version == "waydex-walk-checkpoint/2":
            checkpoint.validate_pair_inventory(self.execution)
        else:
            checkpoint.validate_inventory(self.execution)
        raw = checkpoint.model_dump_json().encode()
        if len(raw) > MAX_CHECKPOINT_BYTES:
            raise ValueError("OD_CHECKPOINT_TRANSPORT_LIMIT")
        self.connection.send_bytes(CHECKPOINT_PREFIX + raw)
        acknowledgment = json.loads(self.connection.recv_bytes(2048))
        if acknowledgment != {
            "ipc_version": 1,
            "batch_id": checkpoint.batch_id,
            "content_hash": checkpoint.content_hash,
        }:
            raise ValueError("OD_CHECKPOINT_ACKNOWLEDGMENT_MISMATCH")
        self.completed.append(checkpoint)
