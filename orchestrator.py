"""Phase 6: パイプライン制御（オーケストレーター）.

ニュース収集 → 台本生成 → スライド生成 → 動画生成 → YouTube投稿を
順次実行し、進捗を管理する。途中再開・単一フェーズ実行に対応。
"""

from __future__ import annotations

import argparse
import json
import logging
import traceback
from datetime import datetime, timezone
from pathlib import Path

logger = logging.getLogger(__name__)

PHASES = ["collect", "script", "slides", "video", "upload"]
SHORTS_PHASES = ["slides_shorts", "video_shorts", "upload_shorts"]


def load_config(config_path: Path = Path("config.json")) -> dict:
    with open(config_path, encoding="utf-8") as f:
        return json.load(f)


# ---------------------------------------------------------------------------
# State management
# ---------------------------------------------------------------------------

def load_state(state_path: Path) -> dict:
    if state_path.exists():
        with open(state_path, encoding="utf-8") as f:
            return json.load(f)
    return {}


def save_state(state_path: Path, state: dict) -> None:
    state_path.parent.mkdir(parents=True, exist_ok=True)
    with open(state_path, "w", encoding="utf-8") as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


# ---------------------------------------------------------------------------
# Phase executors
# ---------------------------------------------------------------------------

def run_collect(date_str: str, config_path: Path, state: dict) -> dict:
    from news_collector import collect_news
    news_path = collect_news(config_path, date_str)
    state["news_path"] = str(news_path)
    state["phases"]["collect"] = "done"
    return state


def run_script(date_str: str, config_path: Path, state: dict, script_type: str) -> dict:
    from script_generator import generate_script
    news_path = Path(state.get("news_path", f"state/news_{date_str}.json"))
    script_path = generate_script(news_path, config_path, script_type)
    state["script_path"] = str(script_path)
    state["phases"]["script"] = "done"
    return state


def run_slides(date_str: str, config_path: Path, state: dict) -> dict:
    from slide_generator import generate_slides
    script_path = Path(state.get("script_path", f"state/script_{date_str}.md"))
    images_dir = generate_slides(script_path, config_path, date_str)
    state["images_dir"] = str(images_dir)
    state["phases"]["slides"] = "done"
    return state


def run_video(date_str: str, config_path: Path, state: dict) -> dict:
    from video_builder import build_video
    video_path = build_video(date_str, config_path)
    state["video_path"] = str(video_path)
    state["phases"]["video"] = "done"
    return state


def run_upload(date_str: str, config_path: Path, state: dict, script_type: str) -> dict:
    from youtube_uploader import upload
    video_url = upload(date_str, config_path, script_type)
    state["video_url"] = video_url
    state["phases"]["upload"] = "done"
    return state


# --- Shorts phase runners ---

def run_slides_shorts(date_str: str, config_path: Path, state: dict) -> dict:
    from slide_generator import generate_slides
    script_path = Path(state.get("script_path", f"state/script_{date_str}.md"))
    images_dir = generate_slides(script_path, config_path, date_str, shorts=True)
    state["images_shorts_dir"] = str(images_dir)
    state["phases"]["slides_shorts"] = "done"
    return state


def run_video_shorts(date_str: str, config_path: Path, state: dict) -> dict:
    from video_builder import build_video
    video_path = build_video(date_str, config_path, shorts=True)
    state["video_shorts_path"] = str(video_path)
    state["phases"]["video_shorts"] = "done"
    return state


def run_upload_shorts(date_str: str, config_path: Path, state: dict, script_type: str) -> dict:
    from youtube_uploader import upload
    video_url = upload(date_str, config_path, script_type, shorts=True)
    state["video_shorts_url"] = video_url
    state["phases"]["upload_shorts"] = "done"
    return state


PHASE_RUNNERS = {
    "collect": lambda d, c, s, t: run_collect(d, c, s),
    "script": lambda d, c, s, t: run_script(d, c, s, t),
    "slides": lambda d, c, s, t: run_slides(d, c, s),
    "video": lambda d, c, s, t: run_video(d, c, s),
    "upload": lambda d, c, s, t: run_upload(d, c, s, t),
    "slides_shorts": lambda d, c, s, t: run_slides_shorts(d, c, s),
    "video_shorts": lambda d, c, s, t: run_video_shorts(d, c, s),
    "upload_shorts": lambda d, c, s, t: run_upload_shorts(d, c, s, t),
}


# ---------------------------------------------------------------------------
# Main pipeline
# ---------------------------------------------------------------------------

def run_pipeline(
    date_str: str | None = None,
    config_path: Path = Path("config.json"),
    step: str | None = None,
    script_type: str = "daily",
    skip_upload: bool = False,
    shorts: bool = False,
    force: bool = False,
) -> dict:
    """Run the full pipeline or a single step.

    Args:
        date_str: Date in YYYYMMDD format. Defaults to today.
        config_path: Path to config.json.
        step: If set, run only this phase.
        script_type: "daily" or "weekly".
        skip_upload: If True, skip the upload phase.
        shorts: If True, also generate and upload Shorts video.
        force: If True, reset all phases and re-run from scratch.

    Returns:
        Final state dict.
    """
    if script_type != "daily":
        raise NotImplementedError(
            f"script_type '{script_type}' is not yet implemented. Only 'daily' is supported."
        )

    config = load_config(config_path)
    state_dir = Path(config["paths"]["state_dir"])

    if date_str is None:
        date_str = datetime.now(timezone.utc).strftime("%Y%m%d")

    state_path = state_dir / f"pipeline_{date_str}.json"
    state = load_state(state_path)

    # Force mode: reset all phases
    if force:
        state["phases"] = {}
        state.pop("error", None)
        logger.info("Force mode: resetting all phases")

    # Initialize state
    if "phases" not in state:
        state["phases"] = {}
    state["date"] = date_str
    state["type"] = script_type
    state["last_run"] = datetime.now(timezone.utc).isoformat()

    # Determine which phases to run
    all_phases = list(PHASES)
    if shorts:
        # Insert Shorts phases after their corresponding regular phases
        # slides → slides_shorts → video → video_shorts → upload → upload_shorts
        all_phases = ["collect", "script", "slides", "slides_shorts",
                      "video", "video_shorts", "upload", "upload_shorts"]

    if step:
        phases_to_run = [step]
    else:
        phases_to_run = [p for p in all_phases if state["phases"].get(p) != "done"]
        if skip_upload:
            phases_to_run = [p for p in phases_to_run if p not in ("upload", "upload_shorts")]

    logger.info(
        "Pipeline: date=%s, type=%s, phases=%s",
        date_str, script_type, phases_to_run,
    )

    for phase in phases_to_run:
        if phase not in PHASE_RUNNERS:
            raise ValueError(f"Unknown phase: {phase}")

        logger.info("--- Starting phase: %s ---", phase)
        state["phases"][phase] = "running"
        save_state(state_path, state)

        try:
            state = PHASE_RUNNERS[phase](date_str, config_path, state, script_type)
            save_state(state_path, state)
            logger.info("--- Phase %s completed ---", phase)
        except Exception as e:
            state["phases"][phase] = "failed"
            state["error"] = {
                "phase": phase,
                "message": str(e),
                "traceback": traceback.format_exc(),
            }
            save_state(state_path, state)
            logger.error("Phase %s failed: %s", phase, e)
            raise

    # Clear previous error on successful completion
    state.pop("error", None)
    save_state(state_path, state)

    logger.info("Pipeline completed for %s", date_str)
    return state


def main() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s: %(message)s",
        datefmt="%H:%M:%S",
    )

    parser = argparse.ArgumentParser(
        description="AI News Video Pipeline Orchestrator",
    )
    parser.add_argument(
        "--date",
        default=None,
        help="Date in YYYYMMDD format (default: today)",
    )
    parser.add_argument(
        "--config",
        default="config.json",
        help="Config file path",
    )
    parser.add_argument(
        "--step",
        default=None,
        choices=PHASES + SHORTS_PHASES,
        help="Run only this phase",
    )
    parser.add_argument(
        "--type",
        default="daily",
        choices=["daily", "weekly"],
        help="Script type",
    )
    parser.add_argument(
        "--skip-upload",
        action="store_true",
        help="Skip YouTube upload phase",
    )
    parser.add_argument(
        "--shorts",
        action="store_true",
        help="Also generate and upload Shorts (9:16) video",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Reset all phases and re-run from scratch",
    )
    args = parser.parse_args()

    state = run_pipeline(
        date_str=args.date,
        config_path=Path(args.config),
        step=args.step,
        script_type=args.type,
        skip_upload=args.skip_upload,
        shorts=args.shorts,
        force=args.force,
    )

    print("\n=== Pipeline Result ===")
    print(json.dumps(state, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
