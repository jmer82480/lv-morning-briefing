# Normalized Item Schema

Every source item, regardless of origin (RSS, API, web scrape), is transformed
into a single flat record with the fields below before it enters the scripting
pipeline.

## Fields

| Field              | Type     | Required | Description                                                                                   |
|--------------------|----------|----------|-----------------------------------------------------------------------------------------------|
| `item_id`          | string   | yes      | Deterministic hash (e.g., SHA-256 of `source_url`). Used for deduplication.                   |
| `source_name`      | string   | yes      | Human-readable name of the source (must match `name` in `starter_sources.csv`).               |
| `source_url`       | string   | yes      | Canonical URL of the original article, page, or API endpoint.                                 |
| `section`          | string   | yes      | One of: `local_news`, `weather`, `traffic`, `local_gov`, `community`.                         |
| `headline`         | string   | yes      | Short title or headline. Max 200 characters.                                                  |
| `summary`          | string   | yes      | Plain-text summary of the item. 1–3 sentences, max 500 characters.                            |
| `body_text`        | string   | no       | Full article or data body if available. Used for deeper context during script generation.      |
| `published_at`     | datetime | yes      | ISO 8601 UTC timestamp of original publication or last meaningful update.                      |
| `retrieved_at`     | datetime | yes      | ISO 8601 UTC timestamp of when the item was fetched.                                          |
| `priority`         | integer  | yes      | 1 (high), 2 (medium), 3 (low). Inherited from source config, may be overridden by relevance.  |
| `location`         | string   | no       | Specific municipality or area if identifiable (e.g., "Bethlehem", "Lehigh County").           |
| `tags`             | list     | no       | Free-form tags for filtering (e.g., `["school-board", "budget"]`).                            |
| `raw_file`         | string   | no       | Relative path to the stored raw response in `raw/` for traceability.                          |

## Notes

- All text fields are plain text (no HTML, no Markdown).
- `published_at` must be within the last 24 hours or the item is discarded
  during normalization.
- Duplicate `item_id` values are dropped; the earliest `retrieved_at` wins.
- Weather and traffic items may have a minimal or absent `body_text`; the
  `summary` field carries the essential information.
