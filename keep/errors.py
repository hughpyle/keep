"""
Error logging utilities for keep CLI.

Logs full stack traces for debugging while showing clean messages to users.
"""

import os
import traceback
from datetime import datetime, timezone
from pathlib import Path

ERROR_LOG_PATH = Path.home() / ".keep" / "errors.log"


def log_exception(exc: Exception, context: str = "") -> Path:
    """
    Log exception with full traceback to file.

    Args:
        exc: The exception that occurred
        context: Optional context string (e.g., command name)

    Returns:
        Path to the error log file
    """
    timestamp = datetime.now(timezone.utc).isoformat()
    ERROR_LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(ERROR_LOG_PATH, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
    with os.fdopen(fd, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"[{timestamp}]")
        if context:
            f.write(f" {context}")
        f.write("\n")
        f.write(traceback.format_exc())
    return ERROR_LOG_PATH
