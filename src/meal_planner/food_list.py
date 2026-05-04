from __future__ import annotations

import csv
from collections.abc import Iterable
from pathlib import Path


def parse_food_list(path: Path) -> list[tuple[str, str | None]]:
    data: list[tuple[str, str | None]] = []
    current: str | None = None
    lines = path.read_text().splitlines(keepends=True)
    for i, line in enumerate(lines):
        ln = line.strip()
        if not ln:
            continue
        prev_blank = i == 0 or not lines[i - 1].strip()
        next_blank = i + 1 >= len(lines) or not lines[i + 1].strip()
        if prev_blank and next_blank:
            current = ln
            continue
        data.append((ln, current))
    return data


def load_food_groups(paths: Iterable[Path]) -> dict[str, str]:
    items: dict[str, str] = {}
    for path in paths:
        if not path or not path.exists():
            continue
        for item, group in parse_food_list(path):
            if group is not None:
                items[item.lower()] = group
    return items


def load_synonyms(path: Path) -> dict[str, str]:
    if not path or not path.exists():
        return {}
    synonyms: dict[str, str] = {}
    with path.open(newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            raw = (row.get("raw") or "").strip().lower()
            canonical = (row.get("canonical") or "").strip().lower()
            if raw and canonical:
                synonyms[raw] = canonical
    return synonyms
