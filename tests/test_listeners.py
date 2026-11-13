"""Listeners that cannot hurt each other, and errors that are logged once."""

from __future__ import annotations

import logging

import pytest

from custom_components.nulleinspeisung.listeners import ErrorLog, Listeners

LOGGER = logging.getLogger("test_listeners")


def test_a_failing_listener_does_not_skip_the_others(
    caplog: pytest.LogCaptureFixture,
) -> None:
    called: list[str] = []

    def first() -> None:
        called.append("first")

    def broken() -> None:
        raise ValueError("broken")

    def last() -> None:
        called.append("last")

    listeners = Listeners(LOGGER)
    for listener in (first, broken, last):
        listeners.add(listener)
    listeners.notify()
    listeners.notify()

    assert called == ["first", "last", "first", "last"]
    errors = [r for r in caplog.records if r.levelno == logging.ERROR]
    assert len(errors) == 1
    assert errors[0].exc_info is not None


def test_a_removed_listener_is_not_called() -> None:
    called: list[int] = []
    listeners = Listeners(LOGGER)
    remove = listeners.add(lambda: called.append(1))
    remove()
    remove()
    listeners.notify()
    assert called == []


def test_every_distinct_error_is_logged_once(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = ErrorLog(LOGGER)
    for _ in range(3):
        log.exception("Planning", ValueError("one"))
    log.exception("Planning", ValueError("two"))
    log.exception("Publishing", ValueError("one"))
    assert len([r for r in caplog.records if r.levelno == logging.ERROR]) == 3
