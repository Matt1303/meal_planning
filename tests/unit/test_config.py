from __future__ import annotations

from pathlib import Path

import pytest
import yaml

from meal_planner.config import Settings


@pytest.mark.unit
def test_load_default_config() -> None:
    settings = Settings.load(Path("config/pipeline.yaml"))
    assert settings.optimizer.min_rating == 3
    assert "Beans" in settings.daily_dozen_targets
    assert "Beans" in settings.portion_sizes


@pytest.mark.unit
def test_invalid_calorie_range(tmp_path: Path) -> None:
    raw = yaml.safe_load(Path("config/pipeline.yaml").read_text())
    raw["optimizer"]["calories_daily_min"] = 3000
    raw["optimizer"]["calories_daily_max"] = 1000
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="calories_daily_min"):
        Settings.load(cfg)


@pytest.mark.unit
def test_missing_portion_size_for_target(tmp_path: Path) -> None:
    raw = yaml.safe_load(Path("config/pipeline.yaml").read_text())
    raw["daily_dozen_targets"]["Imaginary"] = 1
    cfg = tmp_path / "bad.yaml"
    cfg.write_text(yaml.safe_dump(raw))
    with pytest.raises(ValueError, match="portion_sizes"):
        Settings.load(cfg)


@pytest.mark.unit
def test_missing_file() -> None:
    with pytest.raises(FileNotFoundError):
        Settings.load(Path("does/not/exist.yaml"))


@pytest.mark.unit
def test_redacted_dump() -> None:
    from meal_planner.config import settings_to_redacted_dict

    settings = Settings.load(Path("config/pipeline.yaml"))
    settings = settings.model_copy(
        update={"llm": settings.llm.model_copy(update={"api_key": "secret"})}
    )
    redacted = settings_to_redacted_dict(settings)
    assert redacted["llm"]["api_key"] == "***"
