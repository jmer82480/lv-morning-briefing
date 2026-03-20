"""Human review gate for the briefing pipeline.

Opens the editor script for review and waits for operator approval.
"""

import logging
import os
import subprocess
import sys

logger = logging.getLogger("briefing")


def review_script(editor_script_path: str) -> bool:
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


def _open_in_editor(filepath: str):
    """Open a file in the operator's preferred editor."""
    editor = os.environ.get("EDITOR", "")

    if not editor:
        # macOS default
        if sys.platform == "darwin":
            editor = "open"
        else:
            editor = "nano"

    try:
        if editor == "open":
            # macOS 'open' returns immediately, which is fine
            subprocess.run([editor, filepath], check=True)
        else:
            # Terminal editors block until closed
            subprocess.run([editor, filepath], check=True)
    except (subprocess.CalledProcessError, FileNotFoundError) as e:
        logger.warning(f"Could not open editor '{editor}': {e}")
        print(f"Could not open editor. Please manually review: {filepath}")
