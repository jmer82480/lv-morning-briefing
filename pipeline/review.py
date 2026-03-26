"""Human review gate for the briefing pipeline.

Opens the editor script for review and waits for operator approval.
Shows editorial context (selected stories, cuts, overrides) before opening.
"""

import logging
import os
import shlex
import subprocess
import sys

logger = logging.getLogger("briefing")


def review_script(editor_script_path: str, editorial_decisions: dict | None = None) -> bool:
    """Open the editor script for human review. Returns True if approved.

    The editor script is the single editable artifact. Any changes made here
    will be reflected in the speech script, which is generated after approval.

    The operator can:
      - Press Enter to approve
      - Type 'edit' to re-open the editor
      - Type 'abort' to cancel the briefing
    """
    print("\n" + "=" * 60)
    print("HUMAN REVIEW")
    print("=" * 60)

    # Show editorial context if available
    if editorial_decisions:
        _print_review_context(editorial_decisions)

    print(f"\nEditor script: {editor_script_path}")
    print("\nReview and edit the script. Source links and editorial notes are inline.")
    print("Your edits will carry through to the spoken version and audio.\n")

    _open_in_editor(editor_script_path)

    while True:
        try:
            response = input("\n[Enter] to approve | 'edit' to re-open | 'abort' to cancel: ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            print("\nAborted.")
            return False

        if response == "":
            logger.info("Script approved by operator")
            return True
        elif response == "edit":
            _open_in_editor(editor_script_path)
        elif response == "abort":
            logger.info("Script review aborted by operator")
            return False
        else:
            print(f"Unknown command: '{response}'. Try Enter, 'edit', or 'abort'.")


def _print_review_context(decisions: dict):
    """Print a summary of editorial decisions for review context."""
    selected = decisions.get("selected_stories", [])
    cut = decisions.get("cut_stories", [])
    near = decisions.get("near_misses", [])
    weather = decisions.get("weather_decision", {})

    print(f"\n  {len(selected)} stories selected | "
          f"{len(cut)} cut | {len(near)} near misses | "
          f"weather: {weather.get('level', '?')}")

    print("\n  SELECTED (in rank order):")
    for s in sorted(selected, key=lambda x: x.get("rank", 99)):
        rank = s.get("rank", "?")
        headline = s.get("headline", "?")
        source = s.get("source_name", "")
        url = s.get("source_url", "")
        note = s.get("editorial_note", "")
        override = ""
        if s.get("editorial_note", "").startswith("Force-included"):
            override = " [FORCE-INCLUDED]"
        print(f"    {rank}. {headline}")
        print(f"       {source}: {url}{override}")
        if note and not note.startswith("Force-included"):
            print(f"       → {note}")

    if cut:
        print(f"\n  CUT ({len(cut)}):")
        for c in cut[:5]:  # Show top 5 cuts
            print(f"    ✗ {c.get('headline', '?')} — {c.get('reason', '')}")
        if len(cut) > 5:
            print(f"    ... and {len(cut) - 5} more")

    print()


def _open_in_editor(filepath: str):
    """Open a file in the operator's preferred editor.

    On macOS, uses 'open -W -t' to open in the default text editor and
    BLOCK until it closes. This prevents the reviewer from approving
    before they've finished editing.
    """
    editor = os.environ.get("EDITOR", "")

    if editor:
        # $EDITOR may be multi-token (e.g., "code --wait", "subl -w")
        try:
            cmd = shlex.split(editor) + [filepath]
            subprocess.run(cmd, check=True)
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning(f"Could not open $EDITOR '{editor}': {e}")
            # Fall through to platform default

    if sys.platform == "darwin":
        # macOS: 'open -W -t' opens in default text editor and waits for close
        try:
            subprocess.run(["open", "-W", "-t", filepath], check=True)
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning(f"Could not open with 'open -W -t': {e}")
    else:
        # Linux/other: try nano as fallback
        try:
            subprocess.run(["nano", filepath], check=True)
            return
        except (subprocess.CalledProcessError, FileNotFoundError) as e:
            logger.warning(f"Could not open nano: {e}")

    print(f"Could not open editor. Please manually review: {filepath}")
