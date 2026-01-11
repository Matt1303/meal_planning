# Plant-Based Meal Planning System

A weekly meal planning optimization system that generates optimal 7-day meal plans based on plant-based nutritional requirements using mathematical optimization.

## Overview

This system automates the process of creating nutritionally balanced weekly meal plans by:
1. Importing recipes from Paprika Recipe Manager
2. Processing ingredients with AI to categorize plant-based foods
3. Using mathematical optimization (MILP) to generate optimal weekly plans
4. Maximizing ingredient variety while meeting daily nutritional requirements

## Current Workflow (2025 MVP)

### Step 1: Recipe Management in Paprika
1. **Update recipes** in Paprika Recipe Manager app
   - Add new recipes or modify existing ones
   - Ensure proper categorization (Breakfasts, Lunches, Dinner, Snacks)
   - Include all ingredients

### Step 2: Export and Upload to GitHub
2. **Export recipes** from Paprika to HTML format
3. **Upload to GitHub** repository: https://github.com/Matt1303/recipe_html_pages
   - Each recipe becomes an HTML file
   - Git tracks recipe changes via commit history
   - Last modified dates come from Git log

### Step 3: Run the Processing Pipeline

#### Option A: Automated Docker Pipeline (Recommended)
```bash
# 1. Ensure Docker is running
# 2. Set your OpenAI API key in .env file
# 3. Run the complete pipeline
docker-compose up
```

This automatically runs:
- **meal_ingest**: Clones GitHub repo, parses HTML, loads to PostgreSQL
- **data_processor**: Uses OpenAI to extract/categorize ingredients
- **meal_planner**: Runs Pyomo optimization to generate weekly plan

#### Option B: Manual/Development Workflow
```bash
# 1. Start database only
docker-compose up postgres pgadmin

# 2. Run individual steps manually (for development)
python ingest_data.py      # Load recipes from GitHub
python data_processor.py   # Process ingredients with OpenAI
python meal_planner.py     # Generate weekly plan

# OR use the Jupyter notebook for interactive development
jupyter notebook data_processor.ipynb
```

### Step 4: View Results
- **pgAdmin**: http://localhost:8080 (admin@admin.com / root)
- **Query**: `SELECT * FROM meal_planning.weekly_meal_plan ORDER BY week_number DESC, day`
- View the latest weekly plan with all meals and nutritional category counts

## System Architecture

```
Paprika → HTML Export → GitHub → Docker Pipeline → PostgreSQL → Weekly Plan
                                       ↓
                                  OpenAI API
                                  (GPT-4o)
```

### Data Flow

```
┌─────────────────┐
│ Recipe HTML     │
│ (GitHub repo)   │
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│ meal_ingest     │  Extracts: title, ingredients, categories,
│ (ingest_data.py)│  rating, servings, difficulty
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│ recipes table   │  Raw recipe data
└────────┬────────┘
         │
         ↓
┌─────────────────┐
│ data_processor  │  OpenAI parses ingredients →
│ (OpenAI GPT-4o) │  Categories: Beans, Berries, Greens, etc.
└────────┬────────┘  Serving quantities per ingredient
         │
         ↓
┌──────────────────────┐
│ processed_recipes    │  Normalized ingredient-level data
│ table                │  One-hot encoded meal types
└────────┬─────────────┘
         │
         ↓
┌─────────────────┐
│ meal_planner    │  MILP Optimization (Pyomo + GLPK)
│ (Pyomo solver)  │  Objective: Maximize ingredient variety
└────────┬────────┘  Constraints: Daily nutritional requirements
         │
         ↓
┌─────────────────┐
│weekly_meal_plan │  7 days × 4 meals/day = 28 meal slots
│ table           │  Category counts per day
└─────────────────┘
```

## Database Schema

### Table: `meal_planning.recipes`
Raw recipe data from HTML files.

| Column | Type | Description |
|--------|------|-------------|
| id | SERIAL | Primary key |
| title | TEXT | Recipe name (unique) |
| ingredients | TEXT | Semicolon-separated list |
| categories | TEXT | Meal types (comma-separated) |
| rating | INTEGER | Recipe rating |
| servings | TEXT | Serving size |
| difficulty | TEXT | Difficulty level |
| lastmodifieddate | TIMESTAMP | From Git commit history |

### Table: `meal_planning.processed_recipes`
Ingredient-level data with AI categorization.

| Column | Type | Description |
|--------|------|-------------|
| title | TEXT | Recipe name |
| ingredient | TEXT | Single ingredient |
| serving_quantity | TEXT | Amount per serving |
| category | TEXT | Food category (10 categories) |
| breakfasts | INTEGER | 1 if breakfast recipe, 0 otherwise |
| lunches | INTEGER | 1 if lunch recipe, 0 otherwise |
| dinner | INTEGER | 1 if dinner recipe, 0 otherwise |
| snacks | INTEGER | 1 if snack recipe, 0 otherwise |
| lastmodifieddate | TIMESTAMP | Recipe modification date |

**Unique constraint**: (title, ingredient)

### Table: `meal_planning.weekly_meal_plan`
Generated weekly meal plans.

| Column | Type | Description |
|--------|------|-------------|
| run_time | TIMESTAMP | When plan was generated |
| week_number | INTEGER | Incremental week identifier |
| day | INTEGER | Day of week (1-7) |
| breakfasts | TEXT | Recipe title for breakfast |
| lunches | TEXT | Recipe title for lunch |
| dinner | TEXT | Recipe title for dinner |
| snacks | TEXT | Recipe title for snack |
| beans_count | INTEGER | # of bean ingredients that day |
| berries_count | INTEGER | # of berry ingredients |
| other_fruits_count | INTEGER | # of other fruit ingredients |
| cruciferous_vegetables_count | INTEGER | # of cruciferous veggies |
| greens_count | INTEGER | # of greens ingredients |
| other_vegetables_count | INTEGER | # of other veggie ingredients |
| nuts_and_seeds_count | INTEGER | # of nuts/seeds ingredients |
| herbs_and_spices_count | INTEGER | # of herbs/spices |
| whole_grains_count | INTEGER | # of whole grain ingredients |

## Plant-Based Food Categories

The system categorizes ingredients into 10 plant-based food groups (based on Dr. Greger's "Daily Dozen"):

1. **Beans** (3 servings/day target)
2. **Berries** (1 serving/day)
3. **Other Fruits** (2 servings/day)
4. **Cruciferous Vegetables** (1 serving/day)
5. **Greens** (2 servings/day)
6. **Other Vegetables** (2 servings/day)
7. **Nuts and Seeds** (1 serving/day)
8. **Herbs and Spices** (1 serving/day)
9. **Whole Grains** (2 servings/day)
10. **Flaxseeds/Linseeds** (included in Nuts and Seeds)

See [food_list.txt](food_list.txt) for complete categorization reference.

## Optimization Model

### Objective
**Maximize the number of unique ingredients** used across the entire week.

### Decision Variables
- `x[r,d,m]`: Binary - Recipe r assigned to day d, meal type m
- `z[d,i]`: Binary - Ingredient i used on day d
- `y[i]`: Binary - Ingredient i used anywhere in the week

### Constraints
1. **One recipe per meal slot** (28 slots total)
2. **Allowed meal types** (breakfast recipes only for breakfast, etc.)
3. **Recipe repetition limit** (max 5 times per week)
4. **Daily nutritional requirements** (e.g., 3 bean servings, 2 greens servings)
5. **Ingredient usage tracking** (links recipes to ingredients)

### Solver
- **Framework**: Pyomo (Python Optimization Modeling Objects)
- **Solver**: GLPK (GNU Linear Programming Kit)
- **Problem type**: Mixed Integer Linear Programming (MILP)
- **Auto-relaxation**: If infeasible, automatically reduces requirements by 1 (up to 15 steps)

## Configuration

Edit `.env` file to configure:

```bash
# Database
DB_USER=postgres
DB_PASSWORD=postgres
DB_HOST=postgres
DB_PORT=5432
DB_NAME=meal_planning

# Recipe source
REPO_URL=https://github.com/Matt1303/recipe_html_pages

# Processing options
LAST_MODIFIED_DATE='2025-05-12'  # Only process recipes modified after this date
NUM_RECIPES=                      # Leave blank for all, or set number for subset
FORCE_UPDATE_PROCESSED=False      # Set True to reprocess all recipes

# AI API
OPENAI_API_KEY=your_key_here      # Required for ingredient processing
```

## Requirements

### System Requirements
- Docker Desktop (for containerized workflow)
- OR Python 3.12+ (for manual workflow)

### Python Dependencies
See `requirements_*.txt` files:
- **Common**: pandas, SQLAlchemy, psycopg2, GitPython, BeautifulSoup4
- **Data Processor**: openai (GPT-4o API)
- **Meal Planner**: pyomo (GLPK solver)

## Limitations & Known Issues

### Current Limitations
1. **Binary optimization**: Ingredients are either present or not (no quantities)
2. **Equal ingredient weights**: All ingredients count equally toward variety
3. **No cost optimization**: Doesn't consider recipe cost or shopping complexity
4. **Fixed 7-day cycle**: Can't generate partial week plans
5. **No user preferences**: No allergy/exclusion support
6. **Manual export**: Requires Paprika export + GitHub upload

### Known Issues
1. **MEAL_TYPES case inconsistency**: Fixed in claude-improvements branch
2. **SQL injection vulnerabilities**: Fixed in claude-improvements branch
3. **No automated testing**: Planned for future
4. **No error recovery**: Pipeline fails if any step errors

## Future Directions

### Short-term Improvements (Next 3-6 months)

#### 1. Migration to Claude API
- **Why**: More cost-effective, better structured output control
- **Changes**: Replace OpenAI client in `data_processor.py`
- **Benefits**: Lower API costs (~70% reduction), native JSON mode

#### 2. Alternative Database Options
Current PostgreSQL setup works but may be overkill for this use case.

**Options to explore**:
- **SQLite**: Simpler, file-based, no server needed (good for personal use)
- **DuckDB**: Analytics-focused, excellent for read-heavy workflows
- **JSON files**: Simplest option, version-controllable, no DB server
- **Keep PostgreSQL**: If scaling to multi-user or web app

**Recommendation**: Start with SQLite or JSON files for simplicity, move to PostgreSQL only if building web interface.

#### 3. Better Recipe Import Process
Current workflow (Paprika → HTML → GitHub → Clone) is cumbersome.

**Better approaches**:
- **Paprika API**: Direct integration (if API becomes available)
- **Local file monitoring**: Watch Paprika export folder, auto-process
- **Web scraping**: Import directly from recipe websites
- **Manual input**: Simple web form for recipe entry
- **Recipe JSON format**: Standard format, git-trackable, no HTML parsing

#### 4. Improved Optimization Model

**Current limitation**: Binary ingredients (present/not present)

**Proposed improvements**:
- **Gram-based portions**: Track ingredient weights
  - "1 portion = minimum grams" logic
  - More accurate nutritional tracking
- **Multi-objective optimization**:
  - Primary: Meet nutritional requirements
  - Secondary: Maximize variety
  - Tertiary: Minimize cost/complexity
- **Weighted ingredients**: Prioritize nutrient-dense foods
- **Soft constraints**: Allow minor requirement violations with penalties
- **Recipe ratings**: Factor in recipe quality/preferences

**Alternative to Pyomo**:
If Pyomo becomes computationally challenging, consider:
- **Google OR-Tools**: Faster CP-SAT solver, similar API
- **PuLP**: Simpler interface, multiple solvers
- **Heuristic approaches**: Greedy algorithms, simulated annealing
- **Rule-based system**: If perfect optimization isn't required

### Long-term Vision (6-12 months)

#### 5. Web Interface
- View weekly plans in calendar format
- Drag-and-drop meal swapping
- Shopping list generation
- Recipe detail views
- Preference management (allergies, dislikes)

#### 6. Advanced Features
- **Seasonality**: Prefer seasonal produce
- **Leftover optimization**: Plan for using leftovers
- **Prep time optimization**: Balance quick vs. complex meals
- **Shopping optimization**: Minimize store trips
- **Meal prep batching**: Group recipes with similar prep
- **Nutritional analytics**: Track macros, micronutrients over time

#### 7. Mobile App
- Weekly plan view
- Shopping list
- Recipe access while cooking
- Quick substitutions

## Computational Complexity Concerns

> "It seems like using Pyomo would be computationally challenging"

**Current scale**: Not a problem
- ~50-200 recipes
- 28 decision variables (x[r,d,m])
- GLPK solves in <5 seconds

**When it becomes challenging**:
- 1000+ recipes
- Additional constraints (meal prep timing, equipment, etc.)
- Real-time re-optimization

**Solutions if needed**:
1. **Pre-filtering**: Reduce recipe pool before optimization
2. **Decomposition**: Optimize each day separately, then combine
3. **Heuristics**: Use optimization for tough constraints, heuristics for variety
4. **Faster solver**: Commercial solvers (Gurobi, CPLEX) are 10-100x faster
5. **Simplify model**: Remove less important constraints

**Recommendation**: Keep Pyomo for now, it's working well. Only change if you see actual performance issues with your recipe collection size.

## Development Branches

- **main**: Stable production-ready code
- **2025-backup**: Current MVP state (this documentation)
- **claude-improvements**: Security fixes, bug fixes, testing infrastructure
  - Fixed: SQL injection vulnerabilities
  - Fixed: MEAL_TYPES case inconsistency
  - Fixed: Shell command injection
  - Added: .env.example template
  - Improved: .gitignore (checkpoints, cache)

## Getting Started

### First-Time Setup

```bash
# 1. Clone repository
git clone https://github.com/Matt1303/meal_planning.git
cd meal_planning

# 2. Copy .env.example to .env and configure
cp .env.example .env
# Edit .env with your OpenAI API key

# 3. Build and run
docker-compose up
```

### Daily Usage

```bash
# 1. Update recipes in Paprika
# 2. Export to HTML and push to GitHub
# 3. Generate new meal plan
docker-compose up meal_planner

# 4. View results in pgAdmin (http://localhost:8080)
```

## Troubleshooting

### Docker Issues
```bash
# Rebuild containers after code changes
docker-compose build

# View logs
docker-compose logs meal_planner

# Clean restart
docker-compose down -v
docker-compose up
```

### Database Issues
```bash
# Connect to database
docker exec -it meal_planning-postgres-1 psql -U postgres -d meal_planning

# Check tables
\dt meal_planning.*

# View latest plan
SELECT * FROM meal_planning.weekly_meal_plan
WHERE week_number = (SELECT MAX(week_number) FROM meal_planning.weekly_meal_plan)
ORDER BY day;
```

### OpenAI API Issues
- **Rate limits**: Reduce NUM_RECIPES in .env
- **Cost concerns**: Set LAST_MODIFIED_DATE to only process new recipes
- **API errors**: Check OPENAI_API_KEY in .env

## Contributing

This is a personal project, but improvements are welcome!

See [.claude/plans/joyful-tickling-seal.md](.claude/plans/joyful-tickling-seal.md) for the comprehensive improvement plan.

## License

Personal project - see repository for details.

## Acknowledgments

- Plant-based nutrition framework inspired by Dr. Michael Greger's "Daily Dozen"
- Recipe management via Paprika Recipe Manager
- Optimization powered by Pyomo and GLPK
- AI ingredient processing via OpenAI GPT-4o (migrating to Claude)

---

**Last Updated**: January 2026
**Branch**: 2025-backup (MVP baseline)
**Status**: Production-ready MVP
