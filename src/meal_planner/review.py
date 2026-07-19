"""Flag recipes whose ingredient lines have not been reviewed for headings.

A section heading like "Polenta" is indistinguishable from a quantity-less
ingredient line by rule alone — it needs the surrounding lines for context. The
reviewed verdicts live in config/section_headers.csv, so a recipe added or
edited since that review has none, and its headings will be fuzzy-matched into
food and charged a default portion.

This module reports which recipes those are. It deliberately does not guess:
the point is to surface them for a context-aware pass rather than to silently
apply a rule that cannot work.
"""

from __future__ import annotations

import csv
import hashlib
from dataclasses import dataclass
from pathlib import Path

from sqlalchemy import Engine, text

REVIEW_STATE_HEADER = ("recipe_title", "lines_hash")


def ingredient_block_hash(lines: list[str]) -> str:
    """Stable digest of a recipe's ingredient lines, order included.

    Order matters: a heading is identified by what follows it, so a reordered
    block deserves another look even when the same lines are present.
    """
    joined = "\n".join(line.strip() for line in lines)
    return hashlib.sha256(joined.encode()).hexdigest()[:16]


@dataclass(frozen=True)
class UnreviewedRecipe:
    title: str
    lines_hash: str
    quantityless_lines: list[str]

    @property
    def needs_attention(self) -> bool:
        """Only quantity-less lines can be mistaken for a heading."""
        return bool(self.quantityless_lines)


def load_review_state(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}
    with path.open(newline="") as fh:
        return {
            (row.get("recipe_title") or "").strip(): (row.get("lines_hash") or "").strip()
            for row in csv.DictReader(fh)
            if (row.get("recipe_title") or "").strip()
        }


def save_review_state(path: Path, state: dict[str, str]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    with tmp.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(REVIEW_STATE_HEADER)
        writer.writerows(sorted(state.items()))
    tmp.replace(path)


def unreviewed_recipes(engine: Engine, state_path: Path) -> list[UnreviewedRecipe]:
    """Recipes whose ingredient block differs from the one last reviewed."""
    reviewed = load_review_state(state_path)
    with engine.connect() as conn:
        rows = conn.execute(
            text(
                """
                SELECT r.title, ri.raw_text
                FROM meal_planning.recipe r
                JOIN meal_planning.recipe_ingredient ri ON ri.recipe_id = r.recipe_id
                ORDER BY r.title, ri.raw_text
                """
            )
        ).fetchall()

    by_recipe: dict[str, list[str]] = {}
    for title, raw in rows:
        by_recipe.setdefault(str(title), []).append(str(raw))

    out: list[UnreviewedRecipe] = []
    for title, lines in by_recipe.items():
        digest = ingredient_block_hash(lines)
        if reviewed.get(title) == digest:
            continue
        quantityless = [ln for ln in lines if not any(ch.isdigit() for ch in ln)]
        out.append(
            UnreviewedRecipe(title=title, lines_hash=digest, quantityless_lines=quantityless)
        )
    return sorted(out, key=lambda r: (-len(r.quantityless_lines), r.title))
