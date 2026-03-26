import logging
import os
from datetime import date

# Track which log file is currently active so we can swap on date change
_current_log_file: str | None = None


def setup_logger(date_str: str | None = None) -> logging.Logger:
    """Configure logging to stdout and logs/{date}_run.log.

    Safe to call multiple times: swaps the file handler when the date
    changes, and never duplicates the stdout handler.
    """
    global _current_log_file

    if date_str is None:
        date_str = date.today().isoformat()

    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{date_str}_run.log")

    logger = logging.getLogger("briefing")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    if not logger.handlers:
        # First call: set up both handlers
        stdout_handler = logging.StreamHandler()
        stdout_handler.setLevel(logging.INFO)
        stdout_handler.setFormatter(fmt)
        stdout_handler.set_name("briefing_stdout")
        logger.addHandler(stdout_handler)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        file_handler.set_name("briefing_file")
        logger.addHandler(file_handler)
        _current_log_file = log_file

    elif log_file != _current_log_file:
        # Date changed: swap the file handler, keep stdout
        for h in logger.handlers[:]:
            if getattr(h, "name", None) == "briefing_file":
                h.close()
                logger.removeHandler(h)

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(fmt)
        file_handler.set_name("briefing_file")
        logger.addHandler(file_handler)
        _current_log_file = log_file

    return logger
