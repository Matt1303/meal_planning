import os
import yaml


def load_config(path=None):
    cfg_path = path or os.getenv("PIPELINE_CONFIG", "config/pipeline.yaml")
    with open(cfg_path, "r") as f:
        return yaml.safe_load(f)
