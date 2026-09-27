"""Ctrl-C shuts down cleanly: an interrupt that surfaces wrapped in another
exception (threading's "release unlocked lock" when SIGINT lands inside
Event.wait) is reported as an interrupt, not a fatal error."""
from __future__ import annotations

from isharescreen import cli


def _wrapped_interrupt() -> RuntimeError:
    try:
        try:
            raise KeyboardInterrupt
        except KeyboardInterrupt:
            raise RuntimeError("release unlocked lock")
    except RuntimeError as e:
        return e


def test_interrupt_in_context_chain_is_detected():
    assert cli._caused_by_interrupt(_wrapped_interrupt())


def test_plain_errors_are_not_interrupts():
    assert not cli._caused_by_interrupt(RuntimeError("boom"))
    try:
        try:
            raise ValueError("a")
        except ValueError:
            raise RuntimeError("b")
    except RuntimeError as e:
        assert not cli._caused_by_interrupt(e)


def test_main_returns_130_for_wrapped_interrupt(monkeypatch):
    err = _wrapped_interrupt()
    monkeypatch.setattr(cli, "_build_session_config", lambda args: object())

    def _raise(*_a, **_k):
        raise err

    monkeypatch.setattr(cli, "_run_frontend", _raise)
    assert cli.main(["--host", "h", "-u", "u", "--frontend", "desktop"]) == 130
