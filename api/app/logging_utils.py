"""Structured logging and PII redaction utilities.

The application logs operational facts, not customer data.  Account-like values
are scrubbed globally, and holder names should be logged through
``pii_safe_log`` when a message needs a human-readable string.
"""
import json
import logging
import re
import time


ACCOUNT_PATTERNS = [
    re.compile(r"\*+\d{4}"),
    re.compile(r"\b\d{8,}\b"),
]

# Fields that are part of LogRecord itself — not user-supplied extras
_STDLIB_FIELDS = frozenset(logging.LogRecord.__dict__) | {
    "message", "asctime", "exc_text", "stack_info",
}


class PiiRedactionFilter(logging.Filter):
    """Scrub account numbers from log messages before handlers emit them."""
    def filter(self, record: logging.LogRecord) -> bool:
        if record.name == "uvicorn.access":
            return True
        message = record.getMessage()
        for pattern in ACCOUNT_PATTERNS:
            message = pattern.sub("[REDACTED-ACCT]", message)
        record.msg = message
        record.args = ()
        return True


class JsonFormatter(logging.Formatter):
    """Emit one JSON object per log line.

    Suitable for shipping to Loki, Elasticsearch, or any log aggregator that
    expects newline-delimited JSON.  Includes:
      timestamp — ISO-8601 with milliseconds
      level     — DEBUG / INFO / WARNING / ERROR / CRITICAL
      logger    — dotted logger name
      message   — formatted message string
      exception — formatted traceback if present
      ...extras — any fields passed via logger.log(..., extra={...})
    """

    def format(self, record: logging.LogRecord) -> str:
        data: dict = {
            "timestamp": self._ts(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Attach any caller-supplied extra fields
        for key, value in record.__dict__.items():
            if key not in _STDLIB_FIELDS and not key.startswith("_"):
                data[key] = value
        if record.exc_info:
            data["exception"] = self.formatException(record.exc_info)
        return json.dumps(data, default=str, ensure_ascii=False)

    @staticmethod
    def _ts(record: logging.LogRecord) -> str:
        ms = int((record.created - int(record.created)) * 1000)
        return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(record.created)) + f".{ms:03d}Z"


def pii_safe_log(logger: logging.Logger, level: int, message: str, *, holder_names: list[str] | None = None, **fields):
    """Log a message after redacting known holder names from the text."""
    safe_message = message
    for name in holder_names or []:
        if name:
            safe_message = safe_message.replace(name, "[REDACTED-NAME]")
    logger.log(level, safe_message, extra=fields or None)


def configure_pii_logging():
    """Attach the PII redaction filter to every logger."""
    redaction_filter = PiiRedactionFilter()
    root = logging.getLogger()
    root.addFilter(redaction_filter)
    for handler in root.handlers:
        handler.addFilter(redaction_filter)
    for name in ("uvicorn", "uvicorn.error", "celery", "app"):
        logging.getLogger(name).addFilter(redaction_filter)


def configure_json_logging():
    """Replace all handler formatters with the JSON formatter.

    Called when LOG_FORMAT=json.  Must run *after* uvicorn has set up its
    handlers so this formatter overrides them.
    """
    formatter = JsonFormatter()
    root = logging.getLogger()
    for handler in root.handlers:
        handler.setFormatter(formatter)
    for name in ("uvicorn", "uvicorn.error", "uvicorn.access"):
        for handler in logging.getLogger(name).handlers:
            handler.setFormatter(formatter)
