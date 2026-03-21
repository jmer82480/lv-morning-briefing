import logging

import requests

from .base import BaseCollector

logger = logging.getLogger("briefing")


class WeatherCollector(BaseCollector):
    """Collect forecast and alert data from the NWS weather.gov API."""

    def collect(self) -> list[dict]:
        logger.info(f"  Fetching weather: {self.name} ({self.url})")

        try:
            resp = requests.get(self.url, headers=self.headers, timeout=15)
            resp.raise_for_status()
            data = resp.json()
        except requests.RequestException as e:
            logger.warning(f"  Weather API error for {self.name}: {e}")
            return []

        if not self.mode:
            logger.warning(
                f"  Weather source '{self.name}' has no mode set. "
                f"Add mode: forecast or mode: alerts to sources.yaml. Defaulting to forecast."
            )

        if self.mode == "alerts":
            return self._parse_alerts(data)
        else:
            return self._parse_forecast(data)

    def _parse_forecast(self, data: dict) -> list[dict]:
        """Parse NWS forecast response into structured items."""
        periods = data.get("properties", {}).get("periods", [])
        if not periods:
            logger.warning(f"  No forecast periods returned for {self.name}")
            return []

        items = []
        for period in periods[:2]:  # Today + Tonight
            items.append({
                "source_name": self.name,
                "source_url": self.url,
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

        logger.info(f"  Found {len(items)} forecast periods")
        return items

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
