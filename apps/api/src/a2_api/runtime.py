"""Stable configured API factories and worker CLI."""

from a2_api.api.bootstrap import build_app as build_app
from a2_api.api.bootstrap import build_legacy_app as build_legacy_app
from a2_api.api.bootstrap import load_prepare as load_prepare
from a2_api.core.config import RuntimeSettings as RuntimeSettings
from a2_api.db.connection import open_pool as open_pool
from a2_api.workers.cli import main as main
from a2_api.workers.cli import run_worker as run_worker
from a2_api.workers.cli import worker_main as worker_main

if __name__ == "__main__":
    main()
