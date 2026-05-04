from __future__ import annotations

import re
from pathlib import Path
from typing import cast

import yaml


class PlantClassifier:
    def __init__(self, terms: list[str], qualified_milks: list[str]) -> None:
        self._terms = [t.lower() for t in terms]
        self._qualified = [m.lower() for m in qualified_milks]
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
        terms = [str(t) for t in cast(list[object], raw_terms)]
        qualified = [str(m) for m in cast(list[object], raw_qual)]
        return cls(terms=terms, qualified_milks=qualified)

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
