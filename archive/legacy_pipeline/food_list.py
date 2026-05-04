import os
import csv


def parse_food_list(path):
    data = []
    current = None
    with open(path, "r") as f:
        lines = f.readlines()
    for i, line in enumerate(lines):
        ln = line.strip()
        if ln:
            if current is None and (i == 0 or not lines[i - 1].strip()) and (i + 1 < len(lines) and not lines[i + 1].strip()):
                current = ln
                continue
            data.append((ln, current))
        else:
            current = None
    return data


def load_food_groups(paths):
    items = {}
    for path in paths:
        if not path or not os.path.exists(path):
            continue
        for item, group in parse_food_list(path):
            items[item.lower()] = group
    return items


def load_synonyms(path):
    if not path or not os.path.exists(path):
        return {}
    synonyms = {}
    with open(path, newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            raw = (row.get("raw") or "").strip().lower()
            canonical = (row.get("canonical") or "").strip().lower()
            if raw and canonical:
                synonyms[raw] = canonical
    return synonyms
