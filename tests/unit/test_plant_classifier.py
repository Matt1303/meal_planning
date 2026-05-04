from __future__ import annotations

from pathlib import Path

import pytest

from meal_planner.plant import PlantClassifier


@pytest.fixture
def classifier() -> PlantClassifier:
    return PlantClassifier.from_path(Path("config/non_plant_terms.yaml"))


@pytest.mark.unit
def test_plant_is_plant(classifier: PlantClassifier) -> None:
    assert classifier.is_plant("Chickpea curry with spinach and coconut milk")


@pytest.mark.unit
def test_chicken_excluded(classifier: PlantClassifier) -> None:
    assert not classifier.is_plant("Lemon Chicken Orzo")


@pytest.mark.unit
def test_beef_excluded(classifier: PlantClassifier) -> None:
    assert not classifier.is_plant("Simply Perfect Beef Spag Bol")


@pytest.mark.unit
def test_qualified_almond_milk_allowed(classifier: PlantClassifier) -> None:
    assert classifier.is_plant("Smoothie with almond milk and oats")


@pytest.mark.unit
def test_butter_excluded(classifier: PlantClassifier) -> None:
    assert not classifier.is_plant("Pasta with butter")


@pytest.mark.unit
def test_honey_excluded(classifier: PlantClassifier) -> None:
    assert not classifier.is_plant("Granola sweetened with honey")


@pytest.mark.unit
def test_empty_text() -> None:
    classifier = PlantClassifier(terms=["beef"], qualified_milks=[])
    assert classifier.is_plant("")


@pytest.mark.unit
def test_word_boundary() -> None:
    classifier = PlantClassifier(terms=["fish"], qualified_milks=[])
    assert classifier.is_plant("fishbone broth")  # contains "fish" but as part of word
    assert not classifier.is_plant("fresh fish stew")
