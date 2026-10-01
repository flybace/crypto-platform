"""Signal helpers for bounded graceful shutdown of long-running services."""

from __future__ import annotations

from collections.abc import Callable
import logging
import signal


def install_shutdown_handlers(
    callback: Callable[[], None],
    *,
    logger: logging.Logger,
    process_name: str,
) -> Callable[[], None]:
    """Install TERM/INT handlers and return a function that restores them."""
    previous: dict[int, object] = {}

    def handle(signum: int, _frame: object) -> None:
        try:
            signal_name = signal.Signals(signum).name
        except ValueError:
            signal_name = str(signum)
        logger.info("%s received %s; graceful shutdown requested", process_name, signal_name)
        callback()

    for signum in (signal.SIGTERM, signal.SIGINT):
        try:
            previous[int(signum)] = signal.signal(signum, handle)
        except (OSError, ValueError):
            # Signal registration is only available in the main interpreter
            # thread and can be unavailable on a restricted host.
            logger.debug("unable to register %s handler", signum, exc_info=True)

    def restore() -> None:
        for signum, handler in previous.items():
            try:
                signal.signal(signum, handler)
            except (OSError, ValueError):
                logger.debug("unable to restore %s handler", signum, exc_info=True)

    return restore
