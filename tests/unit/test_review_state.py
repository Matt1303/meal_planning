from __future__ import annotations

from pathlib import Path

import pytest

from meal_planner.review import ingredient_block_hash, load_review_state, save_review_state


@pytest.mark.unit
def test_hash_is_stable_and_ignores_surrounding_whitespace() -> None:
    a = ingredient_block_hash(["1 onion", "Polenta", "80 g cornmeal"])
    b = ingredient_block_hash(["1 onion ", " Polenta", "80 g cornmeal "])
    assert a == b


@pytest.mark.unit
def test_hash_changes_when_a_line_is_added() -> None:
    before = ingredient_block_hash(["1 onion", "80 g cornmeal"])
    after = ingredient_block_hash(["1 onion", "80 g cornmeal", "Crispy Shallot Topping"])
    assert before != after


@pytest.mark.unit
def test_hash_changes_when_lines_are_reordered() -> None:
    # Order carries meaning: a heading is identified by what follows it, so a
    # reordered block deserves another look even with the same lines.
    a = ingredient_block_hash(["Polenta", "80 g cornmeal"])
    b = ingredient_block_hash(["80 g cornmeal", "Polenta"])
    assert a != b


@pytest.mark.unit
def test_review_state_round_trips(tmp_path: Path) -> None:
    path = tmp_path / "review.csv"
    state = {"Gumbo": "abc123", "Simple Overnight Oats": "def456"}
    save_review_state(path, state)
    assert load_review_state(path) == state


@pytest.mark.unit
def test_missing_review_state_is_empty_not_an_error(tmp_path: Path) -> None:
    assert load_review_state(tmp_path / "nope.csv") == {}
