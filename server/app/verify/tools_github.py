"""GitHub tool for the verification brain — free, high-signal for builders.

Works unauthenticated (rate-limited); a GITHUB_TOKEN lifts the limit.
"""

import os

import httpx
from claude_agent_sdk import tool

API = "https://api.github.com"


def _text(payload: str) -> dict:
    return {"content": [{"type": "text", "text": payload}]}


def _headers() -> dict:
    headers = {"Accept": "application/vnd.github+json"}
    token = os.environ.get("GITHUB_TOKEN")
    if token:
        headers["Authorization"] = f"Bearer {token}"
    return headers


@tool(
    "github_user",
    "Fetch a public GitHub user's profile and their most recently updated "
    "repositories by login. Free; great for corroborating builder claims.",
    {"login": str},
)
async def github_user(args: dict) -> dict:
    login = args["login"].strip().rstrip("/").split("/")[-1]
    try:
        async with httpx.AsyncClient(timeout=15.0, headers=_headers()) as client:
            u = await client.get(f"{API}/users/{login}")
            if u.status_code == 404:
                return _text(f"No GitHub user '{login}'.")
            u.raise_for_status()
            user = u.json()
            r = await client.get(
                f"{API}/users/{login}/repos",
                params={"sort": "updated", "per_page": 10},
            )
            repos = r.json() if r.status_code == 200 else []
    except Exception as exc:
        return _text(f"github_user error: {type(exc).__name__}: {exc}")

    profile = (
        f"login: {user.get('login')}\n"
        f"name: {user.get('name')}\n"
        f"company: {user.get('company')}\n"
        f"bio: {user.get('bio')}\n"
        f"location: {user.get('location')}\n"
        f"blog: {user.get('blog')}\n"
        f"public_repos: {user.get('public_repos')}\n"
        f"followers: {user.get('followers')}\n"
    )
    repo_lines = [
        f"- {repo.get('name')} (★{repo.get('stargazers_count', 0)}, "
        f"{repo.get('language')}): {repo.get('description') or ''}"
        for repo in repos
        if not repo.get("fork")
    ][:8]
    return _text(profile + "top repos:\n" + "\n".join(repo_lines))
