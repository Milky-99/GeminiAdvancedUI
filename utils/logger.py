# -*- coding: utf-8 -*-
import logging
import sys
from logging.handlers import RotatingFileHandler
from .constants import LOG_FILE, LOGS_DIR, DEFAULT_LOGGING_ENABLED

# --- Basic Configuration ---
LOG_FORMAT = '%(asctime)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s'
DATE_FORMAT = '%Y-%m-%d %H:%M:%S'

# --- Logger Setup ---
logger = logging.getLogger('GeminiApp')
logger.setLevel(logging.DEBUG) # Set base level to DEBUG to capture everything

# Prevent multiple handlers if this module is imported multiple times
if not logger.handlers:
    # --- Console Handler ---
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(logging.INFO) # Show INFO and above on console
    console_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)

    # --- File Handler ---
    # Ensure log directory exists before setting up file handler
    try:
        LOGS_DIR.mkdir(parents=True, exist_ok=True)
        # Rotating file handler (e.g., 5 files of 5MB each)
        file_handler = RotatingFileHandler(
            LOG_FILE, maxBytes=5*1024*1024, backupCount=5, encoding='utf-8'
        )
        file_handler.setLevel(logging.DEBUG) # Log DEBUG and above to file
        file_formatter = logging.Formatter(LOG_FORMAT, datefmt=DATE_FORMAT)
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    except OSError as e:
        logger.error(f"Failed to create log directory or file handler: {e}", exc_info=False)
        # Fallback: only use console handler if file logging fails

# --- Global Logging Toggle ---
# This function needs to be called after settings are loaded
_logging_enabled = DEFAULT_LOGGING_ENABLED # Initial state

def set_logging_enabled(enabled: bool):
    """Globally enable or disable logging output."""
    global _logging_enabled
    _logging_enabled = enabled
    if enabled:
        logger.setLevel(logging.DEBUG)
        logger.info("Logging re-enabled.")
    else:
        logger.info("Disabling logging output...")
        logger.setLevel(logging.CRITICAL + 1) # Effectively disable

def is_logging_enabled() -> bool:
    """Check if logging is currently enabled."""
    return _logging_enabled

# --- Convenience Methods ---
# These methods respect the global enable/disable flag implicitly
# because the logger's level is changed by set_logging_enabled.

def log_debug(msg, *args, **kwargs):
    logger.debug(msg, *args, **kwargs)

def log_info(msg, *args, **kwargs):
    logger.info(msg, *args, **kwargs)

def log_warning(msg, *args, **kwargs):
    logger.warning(msg, *args, **kwargs)

def log_error(msg, *args, exc_info=True, **kwargs):
    # Default to including exception info for errors
    logger.error(msg, *args, exc_info=exc_info, **kwargs)

def log_critical(msg, *args, **kwargs):
    logger.critical(msg, *args, **kwargs)

# Initial state based on default
set_logging_enabled(DEFAULT_LOGGING_ENABLED)