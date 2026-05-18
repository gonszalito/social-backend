Not quite only the DDL.
Running the DDL is the main DB step, but you also need 3 config/runtime steps so the app actually uses Postgres:
Set GOBIG_POSTGRES_ENABLED=1
Set DATABASE_URL to your real Postgres instance
Install new deps (psycopg + psycopg-pool) via pip install -r requirements.txt
If those are done, then yes, the new Postgres write paths should run.
Your actions
Apply schema: run sql/gobig_postgres_schema.sql on your DB.
Update env: GOBIG_POSTGRES_ENABLED=1 and valid DATABASE_URL.
Install dependencies in venv: pip install -r requirements.txt -r requirements-dev.txt.
Restart API service after env/dependency changes.
Verify quickly:
hit POST /api/v1/nlp/ingest with flag ON
hit social writes (/social/follow, /social/recipe-share, /social/potluck/create, /social/potluck/invite)
check rows exist in the new tables.
Reminder: for NLP flag-ON testing, GOBIG_AI_WRAPPER_URL can still point to local mock (http://localhost:8099).
