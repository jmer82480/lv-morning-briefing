"""Project paths and constants."""

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = PROJECT_ROOT / "config"
SOURCES_CSV = CONFIG_DIR / "starter_sources.csv"

REQUIRED_SOURCE_COLUMNS = ["name", "type", "url", "section", "priority"]

VALID_SECTIONS = ["local_news", "weather", "traffic", "local_gov", "community"]
VALID_SOURCE_TYPES = ["rss", "api", "web_scrape"]

SECTION_ORDER = ["local_news", "weather", "traffic", "local_gov", "community"]
