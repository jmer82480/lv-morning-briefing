"""Load and validate config/starter_sources.csv."""

import csv
import sys
from pathlib import Path

from lv_briefing.config import REQUIRED_SOURCE_COLUMNS, SOURCES_CSV


def load_sources(path: Path = SOURCES_CSV) -> list[dict]:
    """Read the sources CSV and validate required columns.

    Returns a list of dicts, one per source row.
    Exits with an error message if the file is missing or columns are invalid.
    """
    if not path.exists():
        print(f"Error: sources file not found: {path}", file=sys.stderr)
        sys.exit(1)

    with open(path, newline="", encoding="utf-8") as f:
        reader = csv.DictReader(f)
        headers = reader.fieldnames or []

        missing = [col for col in REQUIRED_SOURCE_COLUMNS if col not in headers]
        if missing:
            print(
                f"Error: missing required columns in {path.name}: {missing}",
                file=sys.stderr,
            )
            sys.exit(1)

        rows = [row for row in reader if row.get("name", "").strip()]

    return rows
