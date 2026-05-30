"""Persist the per-call Scorecard as JSON — the primary eval signal."""

from pathlib import Path

from loguru import logger

from app.scorecard.schema import Scorecard

# .../server/app/scorecard/writer.py -> parents[2] == .../server
SCORECARDS_DIR = Path(__file__).resolve().parents[2] / "scorecards"


def write_scorecard(card: Scorecard) -> Path:
    SCORECARDS_DIR.mkdir(parents=True, exist_ok=True)
    path = SCORECARDS_DIR / f"{card.call_id}.json"
    path.write_text(card.model_dump_json(indent=2))
    logger.info(f"scorecard written: {path}")
    return path
