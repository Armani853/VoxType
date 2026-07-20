"""Privacy-safe rotating technical logging configuration."""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path

from platformdirs import user_log_path

from voxtype.constants import APP_AUTHOR, APP_NAME


def configure_logging(log_dir: Path | None = None, verbose: bool = False) -> Path:
    directory = log_dir or user_log_path(APP_NAME, APP_AUTHOR)
    directory.mkdir(parents=True, exist_ok=True)
    path = directory / "voxtype.log"
    handler = RotatingFileHandler(
        path, maxBytes=5 * 1024 * 1024, backupCount=4, encoding="utf-8", delay=True
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s", "%Y-%m-%dT%H:%M:%S")
    )
    root = logging.getLogger("voxtype")
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    root.handlers.clear()
    root.addHandler(handler)
    root.propagate = False
    return path

