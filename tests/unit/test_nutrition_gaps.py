from __future__ import annotations

import pytest

from meal_planner.config import ProfileTargets
from meal_planner.ui.data import compute_gaps

TARGETS = ProfileTargets(
    name="matt", calories_daily_min=3100, protein_daily_min=180, fiber_daily_min=30
)


@pytest.mark.unit
def test_meeting_every_target_reports_nothing() -> None:
    gaps = compute_gaps(3200.0, 35.0, 185.0, TARGETS)
    assert not gaps.any_shortfall


@pytest.mark.unit
def test_solver_residue_is_not_a_shortfall() -> None:
    # The solver meets a soft target near-exactly, so a day can land on 179.98 g
    # of a 180 g goal. That was reported as a shortfall and rendered "~0 g",
    # telling the user to go and eat 0 g of protein.
    gaps = compute_gaps(3100.0, 30.0, 179.98, TARGETS)
    assert gaps.protein_g == 0.0
    assert not gaps.any_shortfall


@pytest.mark.unit
def test_a_real_shortfall_is_still_reported() -> None:
    gaps = compute_gaps(3100.0, 30.0, 168.0, TARGETS)
    assert gaps.protein_g == pytest.approx(12.0)
    assert gaps.any_shortfall


@pytest.mark.unit
def test_reported_gaps_never_round_to_zero() -> None:
    # Whatever the threshold, a reported gap must not display as "~0" — that is
    # the self-contradiction this guards against.
    for protein in (179.98, 179.5, 178.4, 175.0, 160.0):
        gaps = compute_gaps(3100.0, 30.0, protein, TARGETS)
        if gaps.protein_g > 0:
            assert f"{gaps.protein_g:.0f}" != "0"


@pytest.mark.unit
def test_absent_targets_are_not_shortfalls() -> None:
    gaps = compute_gaps(0.0, 0.0, 0.0, ProfileTargets(name="nobody"))
    assert not gaps.any_shortfall
