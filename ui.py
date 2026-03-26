"""Lehigh Valley Morning Briefing — Operator UI.

Launch:
    streamlit run ui.py
"""

import json
import os
import platform
import subprocess
import sys
from datetime import date, datetime

import streamlit as st
from dotenv import load_dotenv

# ── Project root ─────────────────────────────────────────────────────────────

BASE_DIR = os.path.dirname(os.path.abspath(__file__))

# Load .env explicitly from the project root (not dependent on CWD)
load_dotenv(os.path.join(BASE_DIR, ".env"))

# ── Constants ────────────────────────────────────────────────────────────────

MODE_LABELS = {
    "full": "Full",
    "review_packet": "Review Packet Only",
    "audio_only": "Audio Only",
}

MODE_STEPS = {"full": 6, "review_packet": 5, "audio_only": 1}

SUCCESS_MSG = {
    "full": "Full briefing completed successfully.",
    "review_packet": "Review packet completed. Audio was not generated.",
    "audio_only": "Audio completed successfully.",
}

# Strings the pipeline prints when entering each stage.
# (marker substring, operator-facing label, step number)
STAGE_MARKERS = [
    ("=== COLLECT", "Collecting sources", 1),
    ("=== NORMALIZE", "Normalizing stories", 2),
    ("=== SELECT STORIES", "Ranking and selecting", 3),
    ("=== GENERATE EDITOR SCRIPT", "Building review packet", 4),
    ("=== GENERATE SPEECH SCRIPT", "Generating speech script", 5),
    ("=== GENERATE AUDIO", "Generating audio", 6),
    ("=== ASSEMBLE", "Assembling outputs", 6),
]

# (display name, relative-path builder, is_directory)
ARTIFACTS = [
    ("Collected stories", lambda d: os.path.join("raw", d), True),
    ("Selected stories", lambda d: os.path.join("selected", d, "editorial_decisions.json"), False),
    ("Review packet", lambda d: os.path.join("scripts", f"{d}_review_packet.md"), False),
    ("Editor script", lambda d: os.path.join("scripts", f"{d}_editor.md"), False),
    ("Speech script", lambda d: os.path.join("scripts", f"{d}_speech.md"), False),
    ("Final audio", lambda d: os.path.join("audio", f"{d}_briefing.mp3"), False),
    ("Run summary", lambda d: os.path.join("logs", f"{d}_summary.json"), False),
]


# ── Helpers ──────────────────────────────────────────────────────────────────


def _abs(rel_path: str) -> str:
    """Resolve a project-relative path to absolute."""
    return os.path.join(BASE_DIR, rel_path)


def _file_exists(rel_path: str, is_dir: bool = False) -> bool:
    """Check whether an artifact exists and is non-trivial."""
    full = _abs(rel_path)
    if is_dir:
        if not os.path.isdir(full):
            return False
        try:
            return any(f.endswith(".json") for f in os.listdir(full))
        except OSError:
            return False
    return os.path.isfile(full) and os.path.getsize(full) > 0


def _any_outputs_exist(date_str: str) -> bool:
    """True if any significant pipeline outputs already exist for this date."""
    return any(
        os.path.isfile(_abs(p))
        for p in [
            f"selected/{date_str}/editorial_decisions.json",
            f"scripts/{date_str}_editor.md",
            f"audio/{date_str}_briefing.mp3",
        ]
    )


def _output_folder(date_str: str) -> str:
    """Best output folder for the date.

    Priority: episodes/{date}/ → scripts/ (if date scripts exist) → project root.
    """
    ep = _abs(f"episodes/{date_str}")
    if os.path.isdir(ep):
        return ep
    # Scripts folder is the most useful target during review workflows
    scripts = _abs("scripts")
    if os.path.isdir(scripts):
        try:
            if any(f.startswith(date_str) for f in os.listdir(scripts)):
                return scripts
        except OSError:
            pass
    return BASE_DIR


def _load_json(rel_path: str):
    """Load a JSON file from a project-relative path, or None."""
    full = _abs(rel_path)
    if os.path.isfile(full):
        try:
            with open(full) as f:
                return json.load(f)
        except (json.JSONDecodeError, OSError):
            return None
    return None


def _check_env_keys(mode: str) -> list[str]:
    """Return missing env vars required for the selected mode."""
    missing = []
    if mode in ("full", "review_packet"):
        if not os.environ.get("ANTHROPIC_API_KEY"):
            missing.append("ANTHROPIC_API_KEY")
    if mode in ("full", "audio_only"):
        if not os.environ.get("ELEVENLABS_API_KEY"):
            missing.append("ELEVENLABS_API_KEY")
    return missing


def _open_path(path: str):
    """Open a file or folder with the OS default handler."""
    if platform.system() == "Darwin":
        subprocess.Popen(["open", path])
    elif platform.system() == "Linux":
        subprocess.Popen(["xdg-open", path])
    else:
        os.startfile(path)  # type: ignore[attr-defined]


# ── Pipeline execution ───────────────────────────────────────────────────────


def _build_cmd(date_str: str, mode: str) -> list[str]:
    """Build the subprocess command for a pipeline run."""
    py = sys.executable
    script = _abs("run_briefing.py")
    if mode == "audio_only":
        return [py, script, "--date", date_str, "--audio-only"]
    cmd = [py, script, "--date", date_str, "--skip-review"]
    if mode == "review_packet":
        cmd.append("--skip-audio")
    return cmd


def _extract_error(log_lines: list[str]) -> str:
    """Pull a plain-English error from log output (searches bottom-up)."""
    for line in reversed(log_lines):
        low = line.lower()
        if "error" in low or "failed" in low:
            return line.strip()
    return "The run exited with an error. Check the logs below."


def _run_pipeline(
    date_str: str,
    mode: str,
    step_el,
    bar_el,
    detail_el,
):
    """Execute the pipeline subprocess with real-time progress updates.

    Returns (success: bool, log_text: str, error_message: str | None).
    """
    cmd = _build_cmd(date_str, mode)
    total = MODE_STEPS[mode]
    env = {**os.environ, "PYTHONUNBUFFERED": "1"}

    proc = subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        text=True,
        bufsize=1,
        cwd=BASE_DIR,
        env=env,
    )

    log_lines: list[str] = []

    for raw_line in proc.stdout:  # type: ignore[union-attr]
        line = raw_line.rstrip()
        log_lines.append(line)

        # Detect stage transitions
        for marker, label, step_num in STAGE_MARKERS:
            if marker in line:
                n = 1 if mode == "audio_only" else min(step_num, total)
                step_el.markdown(
                    f"**Step {n} of {total}** — {label}"
                )
                bar_el.progress(n / total)
                break

        # Show latest detail line
        if line:
            detail_el.text(line[:150])

    proc.wait()
    log_text = "\n".join(log_lines)

    if proc.returncode == 0:
        bar_el.progress(1.0)
        return True, log_text, None

    return False, log_text, _extract_error(log_lines)


# ═════════════════════════════════════════════════════════════════════════════
# Page configuration
# ═════════════════════════════════════════════════════════════════════════════

st.set_page_config(
    page_title="LV Morning Briefing",
    layout="wide",
    initial_sidebar_state="collapsed",
)

# Session-state defaults (set once on first load)
for _key, _default in {
    "run_status": "Ready",
    "run_log": "",
    "run_error": None,
    "run_mode": None,
    "run_started": None,
    "run_finished": None,
    "execute_mode": None,
    "pending_confirm": None,
    "run_success_msg": None,
}.items():
    st.session_state.setdefault(_key, _default)


# ═════════════════════════════════════════════════════════════════════════════
# 1) HEADER
# ═════════════════════════════════════════════════════════════════════════════

h_left, h_right = st.columns([3, 2])

with h_left:
    st.title("Lehigh Valley Morning Briefing")
    st.caption(
        "Run the pipeline, review outputs, and generate audio from one place."
    )

with h_right:
    briefing_date = st.date_input("Briefing date", value=date.today())
    ds = briefing_date.isoformat()

    summary_data = _load_json(f"logs/{ds}_summary.json")
    if summary_data:
        _last_mode = summary_data.get("mode", "?")
        _completed = summary_data.get("completed_at", "")
        try:
            _dt = datetime.fromisoformat(_completed.replace("Z", "+00:00"))
            _completed = _dt.strftime("%I:%M %p")
        except Exception:
            pass
        st.markdown(f"**Run status for selected date:** {_last_mode} at {_completed}")
    else:
        st.markdown("**Run status for selected date:** None")

    st.markdown(f"**Output folder:** `{_output_folder(ds)}`")


# ── Getting Started (compact help) ───────────────────────────────────────────

with st.expander("Getting Started"):
    st.markdown(
        "**Normal workflow:**\n"
        "1. Pick a date above.\n"
        "2. Click **Build Review Packet Only** to collect and rank stories "
        "without generating audio.\n"
        "3. Open the artifacts below to review the editor script and "
        "selected stories.\n"
        "4. Edit the editor script if anything needs changing.\n"
        "5. Click **Audio Only** to generate audio from the reviewed script.\n"
        "6. Use **Run Full Briefing** when you want the complete end-to-end "
        "run in one step."
    )


# ═════════════════════════════════════════════════════════════════════════════
# 2) ACTION BUTTONS
# ═════════════════════════════════════════════════════════════════════════════

st.header("Run Briefing")

b1, b2, b3, b4 = st.columns(4)

with b1:
    btn_full = st.button(
        "Run Full Briefing", use_container_width=True, type="primary"
    )
    st.caption("Collect, rank, script, generate audio, and assemble outputs.")

with b2:
    btn_review = st.button("Build Review Packet Only", use_container_width=True)
    st.caption("Stop before audio so you can inspect stories and scripts first.")

with b3:
    btn_audio = st.button("Audio Only", use_container_width=True)
    st.caption("Generate audio from existing approved script files only.")

with b4:
    btn_open = st.button("Open Output Folder", use_container_width=True)
    st.caption("Open the output folder for the selected date.")

if _any_outputs_exist(ds):
    st.info(
        "Outputs already exist for this date. Rerunning may overwrite files.",
        icon="\u2139\ufe0f",
    )


# ═════════════════════════════════════════════════════════════════════════════
# 3) OPTIONS
# ═════════════════════════════════════════════════════════════════════════════

st.header("Options")

ol, oright = st.columns(2)

with ol:
    opt_open_done = st.checkbox("Open output folder when finished", value=True)
    opt_detail_log = st.checkbox("Show detailed logs", value=False)
    opt_overwrite = st.checkbox(
        "Overwrite existing outputs for this date", value=False
    )

with oright:
    opt_confirm = st.checkbox(
        "Confirm before overwriting existing outputs", value=True
    )

st.caption(
    "Source and prompt settings are managed in the config files, not here."
)


# ═════════════════════════════════════════════════════════════════════════════
# 4) CURRENT RUN
# ═════════════════════════════════════════════════════════════════════════════

st.header("Current Run")

# -- Determine which action was requested this render cycle --

requested: str | None = None
_from_confirm = False

if btn_full:
    requested = "full"
elif btn_review:
    requested = "review_packet"
elif btn_audio:
    requested = "audio_only"
elif btn_open:
    _open_path(_output_folder(ds))

# Resume after overwrite confirmation (set during a previous cycle).
# The user already confirmed, so skip the overwrite check this time.
if not requested and st.session_state.get("execute_mode"):
    requested = st.session_state.execute_mode
    st.session_state.execute_mode = None
    _from_confirm = True

# -- Validate & execute --

if requested:
    # Capture mode and clear stale state immediately, before any checks.
    # This ensures the status row always shows the resolved mode.
    # If this is a fresh button click (not a confirm resumption), clear any
    # stale pending_confirm from a different action so it cannot block.
    if not _from_confirm:
        st.session_state.pending_confirm = None
    label = MODE_LABELS[requested]
    st.session_state.run_mode = label
    st.session_state.run_error = None

    error_msg: str | None = None
    blocked_msg: str | None = None

    # Auth preflight — fail fast with a clear message
    missing_keys = _check_env_keys(requested)
    if missing_keys:
        names = ", ".join(missing_keys)
        error_msg = (
            f"Missing {names} in current environment or .env file. "
            f"Add {'it' if len(missing_keys) == 1 else 'them'} before running."
        )

    # Audio-only prerequisite
    if not error_msg and requested == "audio_only":
        if not os.path.isfile(_abs(f"scripts/{ds}_speech.md")):
            error_msg = (
                "Audio could not run because the speech script was not "
                "found for this date."
            )

    # Overwrite protection (skip for audio-only since it only touches audio/).
    # Also skip when resuming from a confirmed overwrite (from_confirm=True).
    if (
        not error_msg
        and not _from_confirm
        and _any_outputs_exist(ds)
        and requested != "audio_only"
    ):
        if not opt_overwrite:
            blocked_msg = (
                "Outputs already exist for this date. To run again, check "
                "the \"Overwrite existing outputs for this date\" box in "
                "Options above, then try again."
            )
        elif opt_confirm:
            # Store the resolved mode for the confirm dialog (rendered below,
            # outside this block, so it survives Streamlit reruns).
            st.session_state.pending_confirm = requested

    if error_msg:
        st.session_state.run_status = "Failed"
        st.session_state.run_error = error_msg

    elif blocked_msg:
        st.session_state.run_status = "Blocked"
        st.session_state.run_error = blocked_msg

    elif st.session_state.get("pending_confirm"):
        # Waiting for user to confirm overwrite — don't execute yet.
        pass

    else:
        # ── Execute the pipeline ──────────────────────────────────────
        st.session_state.run_started = datetime.now().strftime("%I:%M:%S %p")
        st.session_state.run_finished = None
        st.session_state.run_error = None
        st.session_state.run_status = "Running"

        with st.status(f"Running {label}\u2026", expanded=True) as status_ctx:
            step_el = st.empty()
            bar_el = st.progress(0)
            detail_el = st.empty()

            ok, log, run_err = _run_pipeline(
                ds, requested, step_el, bar_el, detail_el
            )

            st.session_state.run_log = log
            st.session_state.run_finished = datetime.now().strftime(
                "%I:%M:%S %p"
            )

            if ok:
                st.session_state.run_status = "Completed"
                st.session_state.run_error = None
                st.session_state.run_success_msg = SUCCESS_MSG[requested]
                if opt_open_done:
                    _open_path(_output_folder(ds))
            else:
                st.session_state.run_status = "Failed"
                st.session_state.run_error = run_err

        # Rerun so the header, artifacts, and snapshot refresh with new data
        st.rerun()

# -- Overwrite confirmation dialog (rendered outside the action block so
#    clicking Confirm/Cancel triggers a clean rerun without losing state) --

if st.session_state.get("pending_confirm"):
    st.warning(
        "Outputs already exist for this date. Running again will "
        "overwrite files."
    )
    cc1, cc2, _ = st.columns([1, 1, 6])
    with cc1:
        if st.button("Confirm"):
            st.session_state.execute_mode = st.session_state.pending_confirm
            st.session_state.pending_confirm = None
            st.rerun()
    with cc2:
        if st.button("Cancel"):
            st.session_state.pending_confirm = None
            st.rerun()

# -- Persistent status row (always visible) --

_s = st.session_state

# Show completion banner from the run that just finished (survives rerun)
if _s.get("run_success_msg"):
    st.success(_s.run_success_msg)
    st.session_state.run_success_msg = None

sc1, sc2, sc3, sc4, sc5 = st.columns(5)
sc1.metric("Status", _s.run_status)
sc2.metric("Mode", _s.run_mode or "\u2014")
sc3.metric("Started", _s.run_started or "\u2014")
sc4.metric("Finished", _s.run_finished or "\u2014")
sc5.metric("Selected date", ds)

if _s.run_status == "Failed" and _s.run_error:
    st.error(_s.run_error)
elif _s.run_status == "Blocked" and _s.run_error:
    st.warning(_s.run_error)


# ═════════════════════════════════════════════════════════════════════════════
# 5) ARTIFACTS
# ═════════════════════════════════════════════════════════════════════════════

st.header("Artifacts for Selected Date")

# Table header
ah1, ah2, ah3, ah4 = st.columns([3, 1.5, 5, 2])
ah1.markdown("**Artifact**")
ah2.markdown("**Status**")
ah3.markdown("**Path**")
ah4.markdown("**Action**")

st.divider()

for _art_name, _path_fn, _is_dir in ARTIFACTS:
    _rel = _path_fn(ds)
    _full = _abs(_rel)
    _exists = _file_exists(_rel, _is_dir)
    _status_label = "Ready" if _exists else "Missing"

    c1, c2, c3, c4 = st.columns([3, 1.5, 5, 2])
    c1.write(_art_name)
    c2.write(f":green[{_status_label}]" if _exists else f":orange[{_status_label}]")
    c3.caption(_rel)

    if _is_dir and _exists:
        if c4.button("Reveal", key=f"art_{_art_name}"):
            _open_path(_full)
    elif _exists:
        if c4.button("Open", key=f"art_{_art_name}"):
            _open_path(_full)
    else:
        c4.button("Open", key=f"art_{_art_name}", disabled=True)

# Inline audio player (visible whenever audio file exists)
_audio_path = _abs(f"audio/{ds}_briefing.mp3")
if os.path.isfile(_audio_path):
    st.audio(_audio_path)

if st.button("Refresh Artifacts"):
    st.rerun()


# ═════════════════════════════════════════════════════════════════════════════
# 6) TODAY'S SNAPSHOT
# ═════════════════════════════════════════════════════════════════════════════

st.header("Snapshot for Selected Date")

_decisions = _load_json(f"selected/{ds}/editorial_decisions.json")
_raw_dir = _abs(f"raw/{ds}")
_has_raw = os.path.isdir(_raw_dir)

if _decisions or _has_raw:
    # Count stories and weather items from raw files
    _stories_collected = 0
    _weather_items = 0
    if _has_raw:
        for _fn in sorted(os.listdir(_raw_dir)):
            if not _fn.endswith(".json"):
                continue
            try:
                with open(os.path.join(_raw_dir, _fn)) as _f:
                    _items = json.load(_f)
                if not isinstance(_items, list):
                    continue
                for _item in _items:
                    if _item.get("section") == "weather":
                        _weather_items += 1
                    else:
                        _stories_collected += 1
            except Exception:
                pass

    m1, m2, m3 = st.columns(3)

    m1.metric("Stories collected", _stories_collected or "\u2014")

    if _decisions:
        _selected = _decisions.get("selected_stories", [])
        m1.metric("Stories selected", len(_selected))

        _sources = {
            s.get("source_name") for s in _selected if s.get("source_name")
        }
        m2.metric("Local sources represented", len(_sources))
        m2.metric("Weather items", _weather_items or "\u2014")

        _top = sorted(_selected, key=lambda x: x.get("rank", 99))
        _headline = _top[0].get("headline", "\u2014") if _top else "\u2014"
        if len(_headline) > 55:
            _headline = _headline[:55] + "\u2026"
        m3.metric("Top story", _headline)
    else:
        m1.metric("Stories selected", "\u2014")
        m2.metric("Local sources represented", "\u2014")
        m2.metric("Weather items", _weather_items or "\u2014")
        m3.metric("Top story", "\u2014")

    _audio_ready = os.path.isfile(_audio_path)
    m3.metric("Audio status", "Ready" if _audio_ready else "Not generated")

else:
    st.info("No run data available for this date yet.")


# ═════════════════════════════════════════════════════════════════════════════
# 7) LOGS
# ═════════════════════════════════════════════════════════════════════════════

with st.expander("View logs"):
    if st.session_state.run_log:
        st.code(st.session_state.run_log, language="text")
    else:
        st.caption("No log output available. Logs appear after a run.")

    # Optionally show the detailed log file
    if opt_detail_log:
        _log_file = _abs(f"logs/{ds}_run.log")
        if os.path.isfile(_log_file):
            st.markdown("---")
            st.markdown("**Detailed log file:**")
            try:
                with open(_log_file) as _f:
                    st.code(_f.read(), language="text")
            except OSError:
                st.warning("Could not read log file.")
