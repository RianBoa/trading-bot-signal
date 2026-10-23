from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Dict


def narrative_config_path() -> Path:
    return (
        Path(__file__).resolve().parent.parent
        / "config"
        / "strategy"
        / "narrative.json"
    )


def load_narrative_config() -> Dict[str, Any]:
    with narrative_config_path().open(
        encoding="utf-8"
    ) as f:
        return json.load(f)
