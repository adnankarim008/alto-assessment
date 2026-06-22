from __future__ import annotations

"""Application logging configuration.

The app uses standard library logging so logs work locally, under Uvicorn, and in
container platforms without extra dependencies. In production, these stdout logs
can be collected by the platform log agent.
"""

import logging
import os


def configure_logging() -> None:
    """Configure process-wide logging using the `LOG_LEVEL` environment variable."""

    level_name = os.getenv("LOG_LEVEL", "INFO").upper()
    level = getattr(logging, level_name, logging.INFO)
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
    )

    # If another framework configured handlers first, `basicConfig` is a no-op.
    # Setting the root level still lets LOG_LEVEL control app log verbosity.
    logging.getLogger().setLevel(level)
