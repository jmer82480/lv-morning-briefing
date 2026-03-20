"""Hardened model I/O: Claude API calls with safe JSON extraction and validation."""

import json
import logging
import time

import anthropic

logger = logging.getLogger("briefing")


def extract_json(text: str) -> dict:
    """Extract a JSON object from model output.

    Handles:
      - Pure JSON responses
      - JSON wrapped in markdown code blocks (```json ... ```)
      - Nested braces (uses bracket counting, not regex)
    """
    text = text.strip()

    # Try direct parse first
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        pass

    # Strip markdown code fence if present
    if "```" in text:
        # Find content between first ``` and last ```
        lines = text.split("\n")
        in_block = False
        block_lines = []
        for line in lines:
            if line.strip().startswith("```") and not in_block:
                in_block = True
                continue
            elif line.strip() == "```" and in_block:
                in_block = False
                continue
            elif in_block:
                block_lines.append(line)

        if block_lines:
            block_text = "\n".join(block_lines).strip()
            try:
                return json.loads(block_text)
            except json.JSONDecodeError:
                pass

    # Bracket-counting extraction: find the outermost { ... }
    start = text.find("{")
    if start == -1:
        raise json.JSONDecodeError("No JSON object found in response", text, 0)

    depth = 0
    in_string = False
    escape_next = False
    for i in range(start, len(text)):
        c = text[i]
        if escape_next:
            escape_next = False
            continue
        if c == "\\":
            escape_next = True
            continue
        if c == '"' and not escape_next:
            in_string = not in_string
            continue
        if in_string:
            continue
        if c == "{":
            depth += 1
        elif c == "}":
            depth -= 1
            if depth == 0:
                candidate = text[start:i + 1]
                return json.loads(candidate)

    raise json.JSONDecodeError("Unbalanced braces in JSON response", text, start)


def validate_keys(data: dict, required_keys: set[str], label: str = "response") -> None:
    """Validate that all required keys are present in the parsed JSON."""
    missing = required_keys - set(data.keys())
    if missing:
        raise ValueError(f"{label}: missing required keys: {missing}")


def call_claude_json(
    system_prompt: str,
    user_prompt: str,
    model: str,
    max_tokens: int,
    required_keys: set[str],
    label: str = "Claude call",
) -> dict:
    """Call Claude and parse the response as JSON with validation.

    Retries once on failure (API error, parse error, or missing keys).
    """
    client = anthropic.Anthropic()

    for attempt in range(2):
        try:
            response = client.messages.create(
                model=model,
                max_tokens=max_tokens,
                system=system_prompt,
                messages=[{"role": "user", "content": user_prompt}],
            )

            response_text = response.content[0].text
            result = extract_json(response_text)
            validate_keys(result, required_keys, label)
            return result

        except (json.JSONDecodeError, ValueError, anthropic.APIError, KeyError, IndexError) as e:
            if attempt == 0:
                logger.warning(f"{label} failed (attempt 1): {e}. Retrying in 5s...")
                time.sleep(5)
            else:
                logger.error(f"{label} failed after retry: {e}")
                raise
