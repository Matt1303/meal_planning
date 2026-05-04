import os
import json
from .config import load_config


def write_source_inventory(output_path="data/source_inventory.json", config_path=None):
    cfg = load_config(config_path)
    sources = cfg.get("sources", {})
    os.makedirs(os.path.dirname(output_path), exist_ok=True)
    with open(output_path, "w") as f:
        json.dump(sources, f, indent=2)
    return output_path
