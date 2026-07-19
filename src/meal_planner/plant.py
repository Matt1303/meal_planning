from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import yaml


class PlantClassifier:
    def __init__(
        self,
        terms: list[str],
        qualified_milks: list[str],
        qualified_plant_terms: list[str] | None = None,
    ) -> None:
        self._terms = [t.lower() for t in terms]
        # Both lists are exempted the same way: a plant food whose name happens
        # to contain a dairy word ("oat milk", "peanut butter") must not trip it.
        self._qualified = [m.lower() for m in [*qualified_milks, *(qualified_plant_terms or [])]]
        self._term_patterns = [
            re.compile(rf"(?<![\w-]){re.escape(t)}(?![\w-])", re.IGNORECASE) for t in self._terms
        ]
        self._qualified_patterns = [
            re.compile(rf"(?<![\w-]){re.escape(m)}(?![\w-])", re.IGNORECASE)
            for m in self._qualified
        ]

    @classmethod
    def from_path(cls, path: Path) -> PlantClassifier:
        if not path.exists():
            return cls(terms=[], qualified_milks=[])
        with path.open() as fh:
            data = yaml.safe_load(fh) or {}
        if not isinstance(data, dict):
            return cls(terms=[], qualified_milks=[])
        raw_terms = data.get("terms", []) or []
        raw_qual = data.get("qualified_milks", []) or []
        raw_plant = data.get("qualified_plant_terms", []) or []
        terms = [str(t) for t in cast(list[object], raw_terms)]
        qualified = [str(m) for m in cast(list[object], raw_qual)]
        plant_terms = [str(m) for m in cast(list[object], raw_plant)]
        return cls(terms=terms, qualified_milks=qualified, qualified_plant_terms=plant_terms)

    def is_plant(self, text: str) -> bool:
        if not text:
            return True
        haystack = text.lower()
        # exempt qualified milks first by replacing them out
        for pattern in self._qualified_patterns:
            haystack = pattern.sub(" ", haystack)
        for pattern in self._term_patterns:
            if pattern.search(haystack):
                return False
        return True
