"""Instructions and validated configuration for the standalone human HTML build."""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
import tomllib

from dataset_tools.validate import validate_dataset

from .policies import FOUR_WAY, policy_name

ROOT = Path(__file__).resolve().parents[1]
HUMAN_DIR = ROOT / "human"
# These identifiers are part of the published offline bundle and its saved sessions.
BUNDLE_FORMAT = "hanoi_human_standalone_v1"
BUNDLE_FEEDBACK = "goal_rule_4way_v1"


def load_config(path):
    """Load human task settings without starting a server or calling a model API."""
    path = Path(path).expanduser().resolve()
    with path.open("rb") as stream:
        settings = tomllib.load(stream)["experiment"]
    for key in ("T", "k"):
        if type(settings[key]) is not int or settings[key] < 1:
            raise ValueError(f"{key} must be a positive integer")
    if settings["k"] > settings["T"] or type(settings["r1_apply_k"]) is not bool:
        raise ValueError("Require 1 <= k <= T and a boolean r1_apply_k")
    if policy_name(settings.get("feedback_policy")) != FOUR_WAY:
        raise ValueError("The human assessment uses four-way feedback")
    source = (path.parent / settings["dataset"]).resolve()
    raw = source.read_bytes()
    dataset = json.loads(raw)
    validate_dataset(dataset)
    checksum = hashlib.sha256(raw).hexdigest()
    return {
        "dataset": dataset,
        "dataset_sha256": checksum,
        "T": settings["T"],
        "k": settings["k"],
        "r1_apply_k": settings["r1_apply_k"],
        "feedback_policy": BUNDLE_FEEDBACK,
    }
