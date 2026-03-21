Lehigh Valley Morning Briefing

An AI-assisted weekday local audio briefing for the Lehigh Valley.

## What it does

Produces a single ~5-minute audio briefing each weekday morning:
1. **Collect** local news (RSS feeds) and weather (NWS API)
2. **Normalize** and deduplicate items
3. **Cluster** related stories across outlets using fuzzy matching
4. **Select** top 5-8 stories via Claude with editorial rules
5. **Generate** an editor script for human review
6. **Review** — operator edits the script, edits carry through to audio
7. **Generate** a TTS-ready speech script from the reviewed editor version
8. **Synthesize** audio via ElevenLabs TTS
9. **Assemble** the final episode with metadata

## Current sources

- 4 RSS feeds: Morning Call, LehighValleyLive, WFMZ, WLVR
- 2 NWS weather endpoints: forecast + alerts for Lehigh Valley

Traffic, local government calendar, and community event sources are not yet implemented.

## Usage

```bash
# Full pipeline for today
python3 run_briefing.py

# Specific date
python3 run_briefing.py --date 2026-03-20

# Golden day test fixture (no live sources needed)
python3 run_briefing.py --date golden --skip-audio

# Dry run: collect → normalize → select → scorecard (no scripts/audio)
python3 run_briefing.py --dry-run

# Stage reruns
python3 run_briefing.py --collect-only     # Only fetch sources
python3 run_briefing.py --skip-collect     # Re-use existing raw data
python3 run_briefing.py --select-only      # Re-run selection from normalized data
python3 run_briefing.py --script-only      # Re-generate scripts from decisions
python3 run_briefing.py --audio-only       # Re-run TTS only (does not reassemble episode)

# Modifiers
python3 run_briefing.py --skip-review      # Skip human review gate
python3 run_briefing.py --skip-audio       # Generate scripts only
```

## Artifacts per run

```
raw/{date}/                          # Raw collected data per source
normalized/{date}/all_items.json     # Deduplicated, tagged items
selected/{date}/pre_clusters.json    # Clustering audit trail
selected/{date}/editorial_decisions.json
selected/{date}/scorecard.txt        # Editorial scorecard
scripts/{date}_editor.md             # Editable script (source of truth)
scripts/{date}_speech.md             # TTS-ready version
scripts/{date}_review_packet.md      # Review context document
audio/{date}_briefing.mp3            # Final audio
episodes/{date}/                     # Assembled episode + metadata
logs/{date}_run.log                  # Full pipeline log
logs/{date}_summary.json             # Run summary with timing + usage
```

## Configuration

```
config/sources.yaml          # RSS feeds and weather API endpoints
config/settings.yaml         # AI model, TTS voice, clustering thresholds
config/prompt_templates.yaml # Claude prompts for selection and scripting
config/overrides.yaml        # Manual force-include, force-exclude, pin-rank
config/pronunciation.yaml    # TTS pronunciation for local place names
```

## Editorial overrides

`config/overrides.yaml` supports:
- **force_include**: Always include a story (matched by URL across all cluster members)
- **force_exclude**: Always cut a story (matched by headline or URL pattern)
- **pin_rank**: Force a story to a specific rank position

Overrides are enforced deterministically after Claude returns its selection.

## Rerun behavior

- `--audio-only` regenerates only the audio file. It does **not** reassemble the episode package. Use a full run or `--script-only` followed by a full run to update the episode.
- `logs/{date}_summary.json` is overwritten on same-date reruns. The latest run's summary is the one that persists.
- Stage artifacts (`raw/`, `normalized/`, `selected/`, `scripts/`) are overwritten per date, not appended.

## Local Operator UI

A browser-based panel for running the pipeline without using the command line.
No Streamlit account is required — everything runs locally.

```bash
# Set up a virtual environment (one time)
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Launch the operator panel
streamlit run ui.py
```

The UI opens in your default browser and provides three main actions:

- **Run Full Briefing** — Collect sources, rank stories, generate scripts, produce audio, and assemble all outputs end-to-end.
- **Build Review Packet Only** — Run everything except audio so you can inspect stories and scripts before committing to TTS.
- **Audio Only** — Generate audio from an existing speech script (useful after reviewing and editing).

Pick a date, click a button, and watch the progress. Output files can be opened directly from the artifacts table.

## Tests

```bash
# Run all tests
python3 -m pytest tests/ -v

# Golden day fixture (deterministic, no API key needed)
python3 -m pytest tests/test_golden_day.py -v

# Weather day fixture
python3 -m pytest tests/test_weather_day.py -v
```

## Requirements

```bash
pip install -r requirements.txt
```

Requires API keys in `.env`:
- `ANTHROPIC_API_KEY` — Claude (story selection + script generation)
- `ELEVENLABS_API_KEY` — ElevenLabs TTS (audio generation)

## Core editorial rules

- Local first — every item must be relevant to the Lehigh Valley
- ~5 minutes total spoken length
- Plain language, conversational tone
- Source transparency — cite where information comes from
- No opinion — report facts, skip editorializing
- Freshness — only include information from the last 24 hours
