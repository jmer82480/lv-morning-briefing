import logging
import os
from datetime import date


def setup_logger(date_str: str | None = None) -> logging.Logger:
    """Configure logging to stdout and logs/{date}_run.log."""
    if date_str is None:
        date_str = date.today().isoformat()

    log_dir = os.path.join(os.path.dirname(os.path.dirname(__file__)), "logs")
    os.makedirs(log_dir, exist_ok=True)
    log_file = os.path.join(log_dir, f"{date_str}_run.log")

    logger = logging.getLogger("briefing")
    logger.setLevel(logging.DEBUG)

    if logger.handlers:
        return logger

    fmt = logging.Formatter("%(asctime)s [%(levelname)s] %(message)s", datefmt="%H:%M:%S")

    stdout_handler = logging.StreamHandler()
    stdout_handler.setLevel(logging.INFO)
    stdout_handler.setFormatter(fmt)

    file_handler = logging.FileHandler(log_file)
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)

    logger.addHandler(stdout_handler)
    logger.addHandler(file_handler)

    return logger
