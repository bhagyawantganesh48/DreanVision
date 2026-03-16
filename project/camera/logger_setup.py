"""
capture/logger_setup.py
========================
Configures unified logging for the DreamVision capture system.

Call ``setup_logging()`` once at application startup (in main.py or the test
script).  After that, every module obtains its own child logger via the
standard ``logging.getLogger("dreamvision.<module>")`` pattern.

Log destinations
----------------
  1. Console  – coloured, human-readable, level INFO
  2. File     – rotating, JSON-structured, level DEBUG
                stored at data/logs/dreamvision.log
"""

import logging
import logging.handlers
import os
import json
import time

import camera.config as cfg


# ---------------------------------------------------------------------------
# Coloured console formatter
# ---------------------------------------------------------------------------

class _ColourFormatter(logging.Formatter):
    """ANSI colour codes per log level for the terminal output."""

    _COLOURS = {
        logging.DEBUG:    "\033[36m",   # Cyan
        logging.INFO:     "\033[32m",   # Green
        logging.WARNING:  "\033[33m",   # Yellow
        logging.ERROR:    "\033[31m",   # Red
        logging.CRITICAL: "\033[35m",   # Magenta
    }
    _RESET = "\033[0m"

    def format(self, record: logging.LogRecord) -> str:
        colour = self._COLOURS.get(record.levelno, "")
        reset  = self._RESET
        ts     = self.formatTime(record, "%H:%M:%S")
        return (
            f"{colour}[{ts}] [{record.levelname:<8s}] "
            f"{record.name}: {record.getMessage()}{reset}"
        )


# ---------------------------------------------------------------------------
# Structured JSON file formatter
# ---------------------------------------------------------------------------

class _JSONFormatter(logging.Formatter):
    """Formats each log record as a single JSON object on one line."""

    def format(self, record: logging.LogRecord) -> str:
        doc = {
            "ts":       self.formatTime(record, "%Y-%m-%dT%H:%M:%S"),
            "level":    record.levelname,
            "logger":   record.name,
            "message":  record.getMessage(),
        }
        if record.exc_info:
            doc["exception"] = self.formatException(record.exc_info)
        return json.dumps(doc)


# ---------------------------------------------------------------------------
# Public setup function
# ---------------------------------------------------------------------------

def setup_logging(console_level: int = logging.INFO,
                  file_level: int    = logging.DEBUG) -> None:
    """
    Configure the root 'dreamvision' logger with console and rotating-file
    handlers.  Safe to call multiple times (idempotent via handler check).

    Parameters
    ----------
    console_level : int
        Minimum severity for the console handler (default: INFO).
    file_level : int
        Minimum severity for the file handler (default: DEBUG).
    """
    os.makedirs(cfg.LOG_DIR, exist_ok=True)
    log_file = os.path.join(cfg.LOG_DIR, "dreamvision.log")

    root = logging.getLogger("dreamvision")
    root.setLevel(logging.DEBUG)

    # Guard against duplicate handlers when called multiple times
    if root.handlers:
        return

    # Console handler
    ch = logging.StreamHandler()
    ch.setLevel(console_level)
    ch.setFormatter(_ColourFormatter())
    root.addHandler(ch)

    # Rotating file handler  (5 MB × 5 backup files)
    fh = logging.handlers.RotatingFileHandler(
        log_file,
        maxBytes=5 * 1024 * 1024,
        backupCount=5,
        encoding="utf-8",
    )
    fh.setLevel(file_level)
    fh.setFormatter(_JSONFormatter())
    root.addHandler(fh)

    root.info("DreamVision logging initialised — file: %s", log_file)
