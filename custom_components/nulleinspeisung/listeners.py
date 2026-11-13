"""Listeners that cannot hurt each other, and errors that are logged once."""

from __future__ import annotations

import logging
from collections.abc import Callable

from homeassistant.core import CALLBACK_TYPE, callback

MAX_REMEMBERED = 50
"""Distinct errors remembered; when more come, the memory starts afresh."""


class ErrorLog:
    """Logs an error with its traceback once per distinct error.

    A listener that fails on every message of the GX would otherwise flood the log
    several times a second.
    """

    def __init__(self, logger: logging.Logger) -> None:
        """Create it for the logger of the module."""
        self._logger = logger
        self._seen: set[tuple[str, type[BaseException], str]] = set()

    def exception(self, what: str, error: BaseException) -> None:
        """Log that ``what`` failed with ``error``, unless that was logged before."""
        key = (what, type(error), str(error))
        if key in self._seen:
            return
        if len(self._seen) >= MAX_REMEMBERED:
            self._seen.clear()
        self._seen.add(key)
        self._logger.error(
            "%s failed (logged once for this error)", what, exc_info=error
        )


class Listeners:
    """A list of callbacks to call without a failing one skipping the others."""

    def __init__(self, logger: logging.Logger) -> None:
        """Create it empty, logging failures to the logger of the module."""
        self._items: list[Callable[[], None]] = []
        self._errors = ErrorLog(logger)

    @callback
    def add(self, listener: Callable[[], None]) -> CALLBACK_TYPE:
        """Add ``listener``; returns the remover."""
        self._items.append(listener)

        @callback
        def remove() -> None:
            if listener in self._items:
                self._items.remove(listener)

        return remove

    def notify(self) -> None:
        """Call every listener; an exception of one is logged and does not stop
        the others."""
        for listener in list(self._items):
            try:
                listener()
            except Exception as err:
                name = getattr(listener, "__qualname__", repr(listener))
                self._errors.exception(f"Listener {name}", err)
