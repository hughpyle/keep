"""
Error logging utilities for keep CLI.

Logs full stack traces for debugging while showing clean messages to users.
"""

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
    with open(ERROR_LOG_PATH, "a") as f:
        f.write(f"\n{'='*60}\n")
        f.write(f"[{timestamp}]")
        if context:
            f.write(f" {context}")
        f.write("\n")
        f.write(traceback.format_exc())
    return ERROR_LOG_PATH
