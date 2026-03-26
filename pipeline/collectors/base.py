import json
import logging
import os
from abc import ABC, abstractmethod

logger = logging.getLogger("briefing")


class BaseCollector(ABC):
    """Abstract base class for all source collectors."""

    def __init__(self, source_config: dict):
        self.name = source_config["name"]
        self.url = source_config["url"]
        self.section = source_config["section"]
        self.headers = source_config.get("headers", {})
        self.mode = source_config.get("mode", "")

    @abstractmethod
    def collect(self) -> list[dict]:
        """Fetch items from the source. Returns a list of raw item dicts."""

    def save_raw(self, items: list[dict], date_str: str, base_dir: str) -> str:
        """Save raw collected items to raw/{date}/{source_name}.json."""
        out_dir = os.path.join(base_dir, "raw", date_str)
        os.makedirs(out_dir, exist_ok=True)

        filename = self.name.lower().replace(" ", "_") + ".json"
        filepath = os.path.join(out_dir, filename)

        with open(filepath, "w") as f:
            json.dump(items, f, indent=2, default=str)

        logger.info(f"  Saved {len(items)} items to {filepath}")
        return filepath
