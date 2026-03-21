import logging
import re
import time
from datetime import datetime, timezone

import feedparser
import requests
from dateutil import parser as dateparser

from .base import BaseCollector

logger = logging.getLogger("briefing")

RSS_HEADERS = {
    "User-Agent": "LVMorningBriefing/1.0 (lvmorningbriefing@example.com)",
    "Accept": "application/rss+xml, application/xml, text/xml, */*",
}
RSS_TIMEOUT = 15


class RSSCollector(BaseCollector):
    """Collect items from an RSS feed."""

    def collect(self) -> list[dict]:
        logger.info(f"  Fetching RSS: {self.name} ({self.url})")

        body = self._fetch_with_retry(self.url)
        if body is None:
            return []

        feed = feedparser.parse(body)

        if feed.bozo and not feed.entries:
            logger.warning(f"  RSS parse error for {self.name}: {feed.bozo_exception}")
            return []

        items = []
        for entry in feed.entries:
            published_at = self._parse_date(entry)
            if published_at is None:
                continue

            summary = entry.get("summary", "") or ""
            summary = self._strip_html(summary)

            items.append({
                "source_name": self.name,
                "source_url": entry.get("link", ""),
                "section": self.section,
                "headline": entry.get("title", "").strip(),
                "summary": summary[:500],
                "body_text": None,
                "published_at": published_at.isoformat(),
            })

        logger.info(f"  Found {len(items)} items from {self.name}")
        return items

    def _parse_date(self, entry) -> datetime | None:
        """Parse the published date from an RSS entry."""
        for field in ("published", "updated", "created"):
            raw = entry.get(field)
            if raw:
                try:
                    dt = dateparser.parse(raw)
                    if dt.tzinfo is None:
                        dt = dt.replace(tzinfo=timezone.utc)
                    return dt
                except (ValueError, TypeError):
                    continue

        parsed = entry.get("published_parsed") or entry.get("updated_parsed")
        if parsed:
            try:
                return datetime(*parsed[:6], tzinfo=timezone.utc)
            except (ValueError, TypeError):
                pass

        return None

    def _fetch_with_retry(self, url: str, retries: int = 2) -> str | None:
        """Fetch RSS feed body via requests with timeout and retry."""
        headers = {**RSS_HEADERS, **self.headers}
        for attempt in range(retries):
            try:
                resp = requests.get(url, headers=headers, timeout=RSS_TIMEOUT)
                resp.raise_for_status()
                return resp.text
            except requests.RequestException as e:
                if attempt < retries - 1:
                    logger.warning(f"  RSS fetch failed for {self.name} (attempt {attempt + 1}): {e}. Retrying...")
                    time.sleep(3)
                else:
                    logger.error(f"  RSS fetch failed for {self.name} after {retries} attempts: {e}")
                    return None

    def _strip_html(self, text: str) -> str:
        """Remove HTML tags from text."""
        return re.sub(r"<[^>]+>", "", text).strip()
