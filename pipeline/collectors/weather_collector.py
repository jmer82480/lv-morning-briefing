import logging

import requests

from .base import BaseCollector

logger = logging.getLogger("briefing")

# NWS zone forecasts use "detailedForecast" only (no temperature/wind fields).
# We extract conditions from the text for a normalized output.
_ZONE_FORECAST_MARKER = "zones/forecast"


class WeatherCollector(BaseCollector):
    """Collect forecast and alert data from the NWS weather.gov API.

    For forecasts, the collector tries the primary URL first.  If it returns
    no usable periods, it walks through fallback_urls (configured in
    sources.yaml) before giving up.  All sources in the chain are official
    NWS endpoints — different API paths covering the same region.
    """

    def __init__(self, source_config: dict):
        super().__init__(source_config)
        self.fallback_urls: list[str] = source_config.get("fallback_urls", [])

    def collect(self) -> list[dict]:
        logger.info(f"  Fetching weather: {self.name} ({self.url})")

        if self.mode == "forecast":
            return self._collect_forecast_with_fallback()
        elif self.mode == "alerts":
            data = self._fetch_json(self.url)
            return self._parse_alerts(data) if data is not None else []
        else:
            logger.error(
                f"  Weather source '{self.name}' has invalid or missing mode: {self.mode!r}. "
                f"Set mode: forecast or mode: alerts in sources.yaml. Skipping."
            )
            return []

    # ── Forecast with fallback chain ─────────────────────────────────────

    def _collect_forecast_with_fallback(self) -> list[dict]:
        """Try the primary URL, then each fallback, returning the first success."""
        urls = [self.url] + self.fallback_urls

        for i, url in enumerate(urls):
            label = "primary" if i == 0 else f"fallback {i}"
            data = self._fetch_json(url)
            if data is None:
                continue

            if _ZONE_FORECAST_MARKER in url:
                items = self._parse_zone_forecast(data, url, label)
            else:
                items = self._parse_gridpoint_forecast(data, url, label)

            if items:
                return items

        logger.warning(
            f"  {self.name}: all forecast sources failed "
            f"({len(urls)} attempted). No forecast available."
        )
        return []

    def _fetch_json(self, url: str) -> dict | None:
        """Fetch JSON from a URL, returning None on any failure."""
        try:
            resp = requests.get(url, headers=self.headers, timeout=15)
            resp.raise_for_status()
            return resp.json()
        except requests.RequestException as e:
            logger.warning(f"  Weather API error for {url}: {e}")
            return None

    # ── Parsers ──────────────────────────────────────────────────────────

    def _parse_gridpoint_forecast(
        self, data: dict, url: str, label: str
    ) -> list[dict]:
        """Parse NWS gridpoint forecast (has structured temp/wind fields)."""
        periods = data.get("properties", {}).get("periods", [])
        if not periods:
            logger.warning(f"  No forecast periods from {label}: {url}")
            return []

        items = []
        for period in periods[:2]:  # Today + Tonight
            items.append({
                "source_name": f"{self.name} ({label})",
                "source_url": url,
                "section": "weather",
                "type": "forecast",
                "period_name": period.get("name", ""),
                "temperature": period.get("temperature"),
                "temperature_unit": period.get("temperatureUnit", "F"),
                "wind_speed": period.get("windSpeed", ""),
                "wind_direction": period.get("windDirection", ""),
                "short_forecast": period.get("shortForecast", ""),
                "detailed_forecast": period.get("detailedForecast", ""),
            })

        logger.info(f"  Found {len(items)} forecast periods from {label}")
        return items

    def _parse_zone_forecast(
        self, data: dict, url: str, label: str
    ) -> list[dict]:
        """Parse NWS zone forecast (text-only, no structured temp/wind).

        Zone forecasts provide detailedForecast text but not structured
        temperature or wind fields.  We normalize them into the same
        dict shape so downstream code works identically.
        """
        periods = data.get("properties", {}).get("periods", [])
        if not periods:
            logger.warning(f"  No forecast periods from {label}: {url}")
            return []

        items = []
        for period in periods[:2]:
            items.append({
                "source_name": f"{self.name} ({label})",
                "source_url": url,
                "section": "weather",
                "type": "forecast",
                "period_name": period.get("name", ""),
                "temperature": None,
                "temperature_unit": "F",
                "wind_speed": "",
                "wind_direction": "",
                "short_forecast": "",
                "detailed_forecast": period.get("detailedForecast", ""),
            })

        logger.info(f"  Found {len(items)} forecast periods from {label} (zone)")
        return items

    # ── Alerts (no fallback needed — single canonical endpoint) ──────────

    def _parse_alerts(self, data: dict) -> list[dict]:
        """Parse NWS alerts response into structured items."""
        features = data.get("features", [])

        items = []
        for feature in features:
            props = feature.get("properties", {})
            items.append({
                "source_name": self.name,
                "source_url": self.url,
                "section": "weather",
                "type": "alert",
                "event": props.get("event", ""),
                "severity": props.get("severity", ""),
                "headline": props.get("headline", ""),
                "description": props.get("description", ""),
                "onset": props.get("onset", ""),
                "expires": props.get("expires", ""),
                "areas": props.get("areaDesc", ""),
            })

        logger.info(f"  Found {len(items)} active alerts")
        return items
