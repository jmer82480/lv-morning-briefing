# Lehigh Valley Morning Briefing

An AI-assisted weekday local audio briefing for the Lehigh Valley.

## Day-1 MVP Goal

Produce a single ~5-minute audio briefing each weekday morning by pulling local source material, generating a script with AI, and converting it to speech — fully automated, no manual editing required.

## Core Content Sections

1. **Local News** — top stories from Lehigh Valley outlets
2. **Weather** — today's forecast and any alerts
3. **Traffic & Transit** — commute conditions and disruptions
4. **Local Government** — council meetings, public notices, decisions
5. **Community** — events, school updates, things to know

## Core Editorial Rules

- Local first — every item must be relevant to the Lehigh Valley.
- Keep it short — aim for ~5 minutes total.
- Plain language — conversational tone, no jargon.
- Source transparency — cite where information comes from.
- No opinion — report facts, skip editorializing.
- Freshness — only include information from the last 24 hours.

## Folder Overview

```
config/        # Source lists, prompt templates, schedule settings
sources/       # Source definitions and feed URLs
raw/           # Unprocessed data pulled from sources
normalized/    # Cleaned and structured data ready for scripting
scripts/       # Generated briefing scripts (text)
audio/         # Synthesized audio files
episodes/      # Final assembled episodes ready for distribution
logs/          # Run logs and error reports
```
