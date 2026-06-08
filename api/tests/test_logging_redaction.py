import logging

from app.logging_utils import PiiRedactionFilter, pii_safe_log


class CaptureHandler(logging.Handler):
    def __init__(self):
        super().__init__()
        self.messages = []

    def emit(self, record):
        self.messages.append(record.getMessage())


def test_account_patterns_are_redacted_from_logs():
    logger = logging.getLogger("pytest-redaction-account")
    handler = CaptureHandler()
    handler.addFilter(PiiRedactionFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        logger.info("account ****4821 and 123456789012 should not leak")
    finally:
        logger.removeHandler(handler)

    assert handler.messages == ["account [REDACTED-ACCT] and [REDACTED-ACCT] should not leak"]


def test_pii_safe_log_redacts_explicit_holder_names():
    logger = logging.getLogger("pytest-redaction-holder")
    handler = CaptureHandler()
    handler.addFilter(PiiRedactionFilter())
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    logger.propagate = False
    try:
        pii_safe_log(logger, logging.INFO, "reviewed statement for Robert J. Pierre ****4821", holder_names=["Robert J. Pierre"])
    finally:
        logger.removeHandler(handler)

    assert handler.messages == ["reviewed statement for [REDACTED-NAME] [REDACTED-ACCT]"]
