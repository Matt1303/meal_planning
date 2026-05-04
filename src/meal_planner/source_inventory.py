from __future__ import annotations

import json
from pathlib import Path

from meal_planner.config import Settings


def write_source_inventory(
    settings: Settings, output_path: Path = Path("data/source_inventory.json")
) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "local_html": {
            "path": str(settings.sources.local_html.path),
            "selectors": settings.sources.local_html.selectors.model_dump(mode="json"),
        }
    }
    output_path.write_text(json.dumps(payload, indent=2))
    return output_path
