# Recipe fixtures

Tiny HTML fixtures used by integration tests. Each file mirrors the production
recipe HTML structure (h1 title, span recipeYield, p.categories, etc.) and
covers a different parsing edge case:

| File | Edge case |
|---|---|
| `test_simple_lentils.html` | "Lunches, Dinner" pluralisation; small numeric quantities |
| `test_oat_breakfast.html` | volume in ml; mixed quantity/unit; tablespoon |
| `test_chickpea_curry.html` | range yield ("4-6"); whole grains + cruciferous |
| `test_smoothie.html` | snacks tag; flax seeds |
| `test_chicken_dish.html` | non-plant (chicken) — must be filtered out |
