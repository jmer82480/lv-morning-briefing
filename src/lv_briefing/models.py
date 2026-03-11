"""Normalized source item dataclass."""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class SourceItem:
    """A single normalized source item per the normalized_item_schema."""

    item_id: str
    source_name: str
    source_url: str
    section: str
    headline: str
    summary: str
    published_at: datetime
    retrieved_at: datetime
    priority: int
    body_text: Optional[str] = None
    location: Optional[str] = None
    tags: list[str] = field(default_factory=list)
    raw_file: Optional[str] = None
