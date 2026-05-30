"""Meeting task brain.

A Claude Agent SDK agent that performs a meeting participant's request against
EXTERNAL MCP servers (Jira / Slack / Gmail) reached over HTTP with a bearer
token. Same shape as app/verify/brain.py (ClaudeSDKClient query loop), but the
tools live on remote servers we point at via config rather than in-process.

Executes immediately (no confirmation step) and returns ONE short spoken
sentence describing what it did, which the gate speaks back into the meeting.
Only servers with both a URL and a token configured are registered; if none
are, it says so instead of erroring.
"""

from claude_agent_sdk import (
    AssistantMessage,
    ClaudeAgentOptions,
    ClaudeSDKClient,
    ResultMessage,
    TextBlock,
)
from loguru import logger

from app.config import Settings, get_settings

MEETING_SYSTEM = """You are a voice assistant participating in a live meeting. A
participant has just addressed you by name and asked you to do something. Carry
out the request using the available tools (Jira, Slack, Gmail, Linear, Google
Drive) and then reply with ONE short sentence, in plain spoken English, stating
exactly what you did (include the created ticket key, channel, or recipient when
relevant).

Rules:
- Act immediately; do not ask follow-up questions unless the request is
  impossible without a missing required field, in which case say what you need.
- Be literal about what you did. If you could not do it, say so plainly.
- No markdown, no lists, no emojis — your reply is spoken aloud.
- Keep it under 25 words."""

# Runtime-only dynamic MCP servers added via the UI (not persisted to env).
# Maps name → {"url": str, "token": str}
_dynamic_mcps: dict[str, dict] = {}


def _mcp_servers(settings: Settings) -> dict:
    """Register only the external MCP servers that have a URL + token."""
    servers: dict[str, dict] = {}
    specs = [
        ("jira", settings.jira_mcp_url, settings.jira_mcp_token),
        ("slack", settings.slack_mcp_url, settings.slack_mcp_token),
        ("gmail", settings.gmail_mcp_url, settings.gmail_mcp_token),
        ("linear", settings.linear_mcp_url, settings.linear_mcp_token),
        ("google_drive", settings.google_drive_mcp_url, settings.google_drive_mcp_token),
    ]
    for name, url, token in specs:
        if url and token:
            servers[name] = {
                "type": "http",
                "url": url,
                "headers": {"Authorization": f"Bearer {token}"},
            }
    # Merge in runtime-dynamic servers (UI-added), overriding builtins if same name.
    for name, cfg in _dynamic_mcps.items():
        servers[name] = {
            "type": "http",
            "url": cfg["url"],
            "headers": {"Authorization": f"Bearer {cfg['token']}"},
        }
    return servers


_BUILTIN_NAMES = {"jira", "slack", "gmail", "linear", "google_drive"}

_BUILTIN_LABELS = {
    "jira": "Jira",
    "slack": "Slack",
    "gmail": "Gmail",
    "linear": "Linear",
    "google_drive": "Google Drive",
}


def get_active_mcps() -> list[dict]:
    """Return all MCP servers (builtin + dynamic) with connection status."""
    settings = get_settings()
    builtin_specs = [
        ("jira", settings.jira_mcp_url, settings.jira_mcp_token),
        ("slack", settings.slack_mcp_url, settings.slack_mcp_token),
        ("gmail", settings.gmail_mcp_url, settings.gmail_mcp_token),
        ("linear", settings.linear_mcp_url, settings.linear_mcp_token),
        ("google_drive", settings.google_drive_mcp_url, settings.google_drive_mcp_token),
    ]
    result = []
    for name, url, token in builtin_specs:
        connected = bool(url and token) or name in _dynamic_mcps
        result.append({
            "name": name,
            "label": _BUILTIN_LABELS.get(name, name),
            "connected": connected,
            "builtin": True,
        })
    for name, cfg in _dynamic_mcps.items():
        if name not in _BUILTIN_NAMES:
            result.append({
                "name": name,
                "label": cfg.get("label", name),
                "connected": True,
                "builtin": False,
            })
    return result


def set_dynamic_mcps(mcps: list[dict]) -> None:
    """Accept a list of {"name": str, "url": str, "token": str, "label"?: str}
    and store them in the runtime dict. For builtin names this updates the
    runtime URL/token; for new names this adds a custom server."""
    global _dynamic_mcps
    for mcp in mcps:
        name = mcp.get("name", "").strip()
        if not name:
            continue
        _dynamic_mcps[name] = {
            "url": mcp.get("url", ""),
            "token": mcp.get("token", ""),
            "label": mcp.get("label", name),
        }


def remove_dynamic_mcp(name: str) -> bool:
    """Remove a custom (non-builtin) MCP server. Returns True if removed."""
    if name in _BUILTIN_NAMES:
        return False
    return _dynamic_mcps.pop(name, None) is not None


async def handle_request(request: str, transcript: str) -> str:
    """Run one addressed request to completion; return a short spoken summary."""
    settings = get_settings()
    servers = _mcp_servers(settings)
    if not servers:
        return "I don't have any tools connected right now, so I can't do that."

    options = ClaudeAgentOptions(
        system_prompt=MEETING_SYSTEM,
        model=settings.anthropic_model,
        max_turns=settings.meeting_max_turns,
        mcp_servers=servers,
        # Wildcard auto-approves every tool on each configured server and NOTHING
        # else. Preferred over bypassPermissions (which would also disable all
        # other safety prompts) — this grants exactly these MCP servers.
        allowed_tools=[f"mcp__{name}__*" for name in servers],
        strict_mcp_config=True,  # ignore any ambient .mcp.json
        setting_sources=[],  # don't load user/project/local settings or hooks
    )
    prompt = (
        f"Meeting context (rolling extraction, open tasks, then the latest lines):\n"
        f"{transcript or '(none)'}\n\n"
        f"The request just made to you: {request}\n\n"
        "Do it now with the available tools, then reply with one short spoken sentence."
    )

    result_text = ""
    accumulated = ""
    async with ClaudeSDKClient(options=options) as client:
        await client.query(prompt)
        async for msg in client.receive_response():
            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, TextBlock):
                        accumulated += block.text
            elif isinstance(msg, ResultMessage):
                result_text = msg.result or ""

    summary = (result_text or accumulated).strip()
    if not summary:
        logger.warning("meeting brain returned no text")
        return "Done."
    return summary
