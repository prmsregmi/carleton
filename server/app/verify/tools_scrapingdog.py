"""ScrapingDog tools for the verification brain.

Credit discipline: Google search is cheap (5 credits); the LinkedIn profile
endpoint is 50 credits, so its description tells the brain to call it only for
the single best candidate, once. All failures degrade to a text note rather
than raising — the brain must always be able to reach a verdict.
"""

import os

import httpx
from claude_agent_sdk import tool

GOOGLE_URL = "https://api.scrapingdog.com/google"
LINKEDIN_URL = "https://api.scrapingdog.com/profile/"


def _text(payload: str) -> dict:
    return {"content": [{"type": "text", "text": payload}]}


@tool(
    "google_search",
    "Google search to find a person's public profiles (LinkedIn/X/GitHub) or "
    "evidence for a claim. Returns the top organic results. Cheap — use freely.",
    {"query": str},
)
async def google_search(args: dict) -> dict:
    key = os.environ.get("SCRAPINGDOG_API_KEY")
    if not key:
        return _text("google_search unavailable: SCRAPINGDOG_API_KEY not set")
    try:
        async with httpx.AsyncClient(timeout=15.0) as client:
            resp = await client.get(
                GOOGLE_URL,
                params={"api_key": key, "query": args["query"], "results": 10},
            )
        resp.raise_for_status()
        organic = (resp.json() or {}).get("organic_data", [])[:10]
    except Exception as exc:
        return _text(f"google_search error: {type(exc).__name__}: {exc}")

    if not organic:
        return _text("No organic results.")
    lines = [
        f"{i + 1}. {r.get('title', '')}\n   {r.get('link', '')}\n   {r.get('snippet', '')}"
        for i, r in enumerate(organic)
    ]
    return _text("\n".join(lines))


@tool(
    "linkedin_profile",
    "Fetch a public LinkedIn profile by its slug (the part after /in/, e.g. "
    "'satyanadella'). COSTS 50 credits — call at most once, only for the single "
    "best candidate confirmed by a google_search hit.",
    {"slug": str},
)
async def linkedin_profile(args: dict) -> dict:
    key = os.environ.get("SCRAPINGDOG_API_KEY")
    if not key:
        return _text("linkedin_profile unavailable: SCRAPINGDOG_API_KEY not set")
    slug = args["slug"].strip().rstrip("/").split("/")[-1]
    try:
        async with httpx.AsyncClient(timeout=25.0) as client:
            resp = await client.get(
                LINKEDIN_URL,
                params={"api_key": key, "type": "profile", "id": slug},
            )
        resp.raise_for_status()
    except Exception as exc:
        return _text(f"linkedin_profile error: {type(exc).__name__}: {exc}")
    # Return raw JSON text; the brain extracts the fields it needs.
    return _text(resp.text[:6000])
