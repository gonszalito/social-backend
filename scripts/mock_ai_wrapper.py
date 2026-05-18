"""
Local stand-in for gobig-ai-wrapper. Run from repo root:

    uvicorn scripts.mock_ai_wrapper:app --port 8099

Set ``GOBIG_AI_WRAPPER_URL=http://127.0.0.1:8099`` when exercising flag-ON NLP ingest manually.
"""

from fastapi import FastAPI

app = FastAPI(title="gobig-ai-wrapper (mock)")


@app.post("/nlp/enrich")
async def mock_nlp_enrich(body: dict) -> dict:
    ctx = body.get("context") or {}
    recipes = ctx.get("recipes") or []
    n = len(recipes)
    return {
        "enriched_count": n,
        "enriched_recipe_count": n,
        "enrichment_version": "mock-v1",
    }


@app.post("/sentry/triage")
async def mock_sentry_triage(body: dict) -> dict:
    return {
        "root_cause": "Mock root cause — replace when real wrapper is live",
        "suggested_fix": "Mock suggested fix — replace when real wrapper is live",
    }
