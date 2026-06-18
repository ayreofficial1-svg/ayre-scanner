"""
utils/debug_logger.py
────────────────────
Enhanced logging system for diagnostics.
Logs critical failures to both console and file.
"""

import sys
import os
import logging
from datetime import datetime

# Create logs directory if it doesn't exist
_LOG_DIR = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "logs")
os.makedirs(_LOG_DIR, exist_ok=True)

# Create logger
logger = logging.getLogger("stockscanner")
logger.setLevel(logging.DEBUG)

# File handler (always logs everything)
_log_file = os.path.join(_LOG_DIR, f"scanner_{datetime.now().strftime('%Y%m%d_%H%M%S')}.log")
file_handler = logging.FileHandler(_log_file)
file_handler.setLevel(logging.DEBUG)

# Console handler (logs WARNING and above by default)
console_handler = logging.StreamHandler(sys.stdout)
console_handler.setLevel(logging.WARNING)

# Formatter
formatter = logging.Formatter(
    fmt='%(asctime)s | %(levelname)-8s | %(name)s | %(message)s',
    datefmt='%Y-%m-%d %H:%M:%S'
)
file_handler.setFormatter(formatter)
console_handler.setFormatter(formatter)

# Add handlers
logger.addHandler(file_handler)
logger.addHandler(console_handler)

def set_console_level(level):
    """Change console logging level (e.g., to DEBUG for verbose mode)."""
    console_handler.setLevel(level)

def error_summary():
    """Return summary of errors logged."""
    return {
        "log_file": _log_file,
        "exists": os.path.exists(_log_file),
        "size_bytes": os.path.getsize(_log_file) if os.path.exists(_log_file) else 0,
    }
