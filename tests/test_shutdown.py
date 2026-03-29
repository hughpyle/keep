"""Tests for process-global shutdown coordination."""

from keep.shutdown import clear_shutdown, is_shutting_down, request_shutdown


def test_clear_shutdown_resets_event():
    clear_shutdown()
    request_shutdown()
    assert is_shutting_down() is True

    clear_shutdown()

    assert is_shutting_down() is False
