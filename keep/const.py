"""Centralized constants that determine system behavior.

Import from here instead of hardcoding magic numbers.
Add new constants here when a value is used in more than one module.
"""

# -- Database filenames (relative to store_path) --
WORK_QUEUE_DB = "continuation.db"
PENDING_SUMMARIES_DB = "pending_summaries.db"
DOCUMENTS_DB = "documents.db"

# -- Daemon --
DAEMON_PORT = 5337
DAEMON_PORT_FILE = ".daemon.port"
DAEMON_TOKEN_FILE = ".daemon.token"
DAEMON_PID_FILE = ".daemon.pid"
OPS_LOG_FILE = "keep-ops.log"

# -- SQLite --
SQLITE_BUSY_TIMEOUT_MS = 5000

# -- Work queue / processing --
DEFAULT_LEASE_SECONDS = 120
BACKOFF_BASE_SECONDS = 30
BACKOFF_MAX_SECONDS = 3600

# -- Logging --
OPS_LOG_MAX_BYTES = 1_000_000
OPS_LOG_BACKUP_COUNT = 3
