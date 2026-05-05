"""Lazy stdlib `logging` initialiser with Rich handler.

Console output for the user stays as `rich.print` calls inside renderers.
Logs are for postmortem / debugging — they go to stderr at WARNING+ by
default, or DEBUG if `UPWORK_INTEL_DEBUG=1` in the env.
"""
import logging
import os
import sys
from logging import Logger

_INITIALISED = False


def _init() -> None:
    global _INITIALISED
    if _INITIALISED:
        return

    level_name = os.getenv("UPWORK_INTEL_LOG", "WARNING").upper()
    level = getattr(logging, level_name, logging.WARNING)

    try:
        from rich.logging import RichHandler

        handler: logging.Handler = RichHandler(
            rich_tracebacks=True,
            show_path=False,
            show_time=True,
            markup=False,
            log_time_format="%H:%M:%S",
        )
        handler.setFormatter(logging.Formatter("%(message)s"))
    except ImportError:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
        )

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(level)

    # Quiet noisy libs.
    logging.getLogger("urllib3").setLevel(logging.WARNING)
    logging.getLogger("requests").setLevel(logging.WARNING)

    _INITIALISED = True


def get_logger(name: str) -> Logger:
    """Get a logger; initialises root once on first call."""
    _init()
    return logging.getLogger(name)
