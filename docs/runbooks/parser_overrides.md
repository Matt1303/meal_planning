# Runbook: managing parser overrides

The parser resolves canonical ingredient names with the following
precedence:

1. **`ingredient_override` table** — exact match on lowercased raw text.
2. **`ingredient_synonyms.csv`** — exact match on lowercased raw text or
   the cleaned ingredient name.
3. **rapidfuzz fuzzy match** against the canonical food list (cutoff =
   `parse.fuzzy_min_score`).
4. **LLM fallback** — only if `LLM_API_KEY` is configured.

## Adding an override

```bash
meal-planner override add \
  --raw "1 tbsp gochujang"          # exactly the raw text from the recipe HTML
  --canonical "gochujang" \
  --group "Herbs and Spices"
```

The CLI lowercases the raw text before inserting. List and remove:

```bash
meal-planner override list
meal-planner override remove --raw "1 tbsp gochujang"
```

After adding overrides, re-run the parser to repopulate
`recipe_ingredient`:

```bash
meal-planner parse
```

## When to use overrides vs synonyms

- **Synonyms** (CSV in `config/`) are the right home for **vocabulary**
  mappings that apply across recipes — e.g. `aubergine → aubergine (eggplant)`.
- **Overrides** (DB-managed) are right for **specific raw lines** that the
  fuzzy matcher gets wrong on a single recipe — e.g. `"1 small jar passata"
  → "tomatoes"`.
