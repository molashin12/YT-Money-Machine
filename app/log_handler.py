"""
In-memory log handler — captures all application logs into a ring buffer
so they can be streamed to the admin dashboard in real time.
"""

import logging
from collections import deque
from datetime import datetime
from threading import Lock


class LogRecord:
    """A simplified log record for JSON serialization."""
    __slots__ = ("timestamp", "level", "logger", "message")

    def __init__(self, timestamp: str, level: str, logger: str, message: str):
        self.timestamp = timestamp
        self.level = level
        self.logger = logger
        self.message = message

    def to_dict(self) -> dict:
        return {
            "timestamp": self.timestamp,
            "level": self.level,
            "logger": self.logger,
            "message": self.message,
        }


class InMemoryLogHandler(logging.Handler):
    """
    A logging handler that stores the last N log records in memory.
    Thread-safe via a lock on the deque.
    """

    def __init__(self, max_records: int = 500):
        super().__init__()
        self.records: deque[LogRecord] = deque(maxlen=max_records)
        self._lock = Lock()
        self._counter = 0  # monotonic counter for polling

    def emit(self, record: logging.LogRecord):
        try:
            entry = LogRecord(
                timestamp=datetime.fromtimestamp(record.created).strftime("%Y-%m-%d %H:%M:%S"),
                level=record.levelname,
                logger=record.name,
                message=self.format(record),
            )
            with self._lock:
                self._counter += 1
                self.records.append(entry)
        except Exception:
            self.handleError(record)

    def get_logs(self, after: int = 0, limit: int = 200) -> tuple[list[dict], int]:
        """
        Get log records after a given counter position.
        Returns (list_of_dicts, current_counter).
        """
        with self._lock:
            total = len(self.records)
            # Calculate how many new logs since 'after'
            new_count = self._counter - after
            if new_count <= 0:
                return [], self._counter

            # Get the last 'new_count' records (capped by limit and buffer size)
            start = max(0, total - min(new_count, limit, total))
            logs = [r.to_dict() for r in list(self.records)[start:]]
            return logs, self._counter

    def get_all(self, limit: int = 200) -> tuple[list[dict], int]:
        """Get the most recent logs."""
        with self._lock:
            logs = [r.to_dict() for r in list(self.records)[-limit:]]
            return logs, self._counter


# ── Singleton ──
_handler: InMemoryLogHandler | None = None


def get_log_handler() -> InMemoryLogHandler:
    """Get or create the singleton log handler."""
    global _handler
    if _handler is None:
        _handler = InMemoryLogHandler(max_records=500)
        _handler.setFormatter(logging.Formatter("%(message)s"))
    return _handler


def install_log_handler():
    """Install the in-memory handler on the root logger."""
    handler = get_log_handler()
    root = logging.getLogger()
    # Avoid duplicates
    if handler not in root.handlers:
        root.addHandler(handler)
