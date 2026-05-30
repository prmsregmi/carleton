import asyncio
import os
import sys
import uuid
from datetime import UTC, datetime, timezone

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "..", "server"))

from agent.meet_bot import join_meet
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from app.config import get_settings
from app.meeting.brain import get_active_mcps, set_dynamic_mcps, remove_dynamic_mcp

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

# ── State ──────────────────────────────────────────────────────────────────────

connected_clients: list[WebSocket] = []

meetings: dict[str, dict] = {}  # meeting_id → meeting state


def meeting_snapshot(mid: str) -> dict:
    m = meetings[mid]
    return {
        "meeting_id": mid,
        "url": m["url"],
        "state": m["state"],
        "status": m["status"],
        "started_at": m["started_at"],
        "transcript_count": len(m["transcript"]),
        "action_count": len(m["actions"]),
    }


# ── Broadcast ──────────────────────────────────────────────────────────────────


async def broadcast(message: dict):
    dead = []
    for ws in connected_clients:
        try:
            await ws.send_json(message)
        except Exception:
            dead.append(ws)
    for ws in dead:
        connected_clients.remove(ws)


# ── Routes ─────────────────────────────────────────────────────────────────────


class JoinRequest(BaseModel):
    meeting_url: str


@app.post("/api/join")
async def join_meeting(req: JoinRequest):
    mid = str(uuid.uuid4())[:8]
    meetings[mid] = {
        "url": req.meeting_url,
        "state": "joining",
        "status": "Starting bot...",
        "started_at": datetime.now(UTC).isoformat(),
        "transcript": [],
        "actions": [],
        # Latest rolling extraction (context + tasks) for the dashboard, and the
        # live session the run-tasks endpoint drives the batch runner against.
        "extraction": None,
        "session": None,
    }
    asyncio.create_task(run_bot(mid, req.meeting_url))
    return {"meeting_id": mid}


@app.get("/api/meetings")
async def list_meetings():
    return [meeting_snapshot(mid) for mid in meetings]


@app.get("/api/config")
async def get_config():
    return {"bot_name": get_settings().meeting_bot_name}


@app.get("/api/meetings/{mid}/extraction")
async def get_extraction(mid: str):
    """Latest rolling extraction (context + tasks) so a freshly-connected dashboard
    can render the current state without waiting for the next tick."""
    m = meetings.get(mid)
    if m is None:
        return {"error": "unknown meeting"}
    return {"meeting_id": mid, "extraction": m.get("extraction")}


@app.post("/api/meetings/{mid}/run-tasks")
async def run_tasks(mid: str):
    """Passive path: execute every pending task through the SAME interface the wake
    word uses (`app.meeting.brain.handle_request`) via the batch runner. Each result
    is broadcast as it lands; the updated extraction is pushed at the end."""
    m = meetings.get(mid)
    if m is None:
        return {"error": "unknown meeting"}
    session = m.get("session")
    if session is None:
        return {"error": "meeting not live"}

    from app.extraction.batch import run_pending_tasks
    from app.meeting.brain import handle_request

    async def on_result(task: str, result: str):
        meetings[mid]["actions"].append(result)
        await broadcast({"type": "assistant", "meeting_id": mid, "text": result})

    results = await run_pending_tasks(session, handle_request, on_result=on_result)
    data = session.extraction.model_dump()
    meetings[mid]["extraction"] = data
    await broadcast({"type": "extraction", "meeting_id": mid, "extraction": data})
    return {"ran": len(results), "results": [{"task": t, "result": r} for t, r in results]}


# ── MCP management ─────────────────────────────────────────────────────────────


class McpUpsertRequest(BaseModel):
    name: str
    label: str = ""
    url: str
    token: str


@app.get("/api/mcps")
async def list_mcps():
    return get_active_mcps()


@app.post("/api/mcps")
async def upsert_mcp(req: McpUpsertRequest):
    set_dynamic_mcps([{"name": req.name, "label": req.label or req.name, "url": req.url, "token": req.token}])
    return {"ok": True}


@app.delete("/api/mcps/{name}")
async def delete_mcp(name: str):
    removed = remove_dynamic_mcp(name)
    if not removed:
        from fastapi import HTTPException
        raise HTTPException(status_code=400, detail="Cannot remove builtin MCP or not found")
    return {"ok": True}


@app.websocket("/ws")
async def websocket_endpoint(ws: WebSocket):
    await ws.accept()
    connected_clients.append(ws)
    # Send current meetings state immediately on connect
    await ws.send_json(
        {"type": "meetings", "meetings": [meeting_snapshot(mid) for mid in meetings]}
    )
    try:
        while True:
            await ws.receive_text()
    except WebSocketDisconnect:
        if ws in connected_clients:
            connected_clients.remove(ws)


# ── Bot runner ─────────────────────────────────────────────────────────────────


async def update_meeting(mid: str, **kwargs):
    meetings[mid].update(kwargs)
    await broadcast({"type": "meeting_update", "meeting_id": mid, **kwargs})


async def run_bot(mid: str, meeting_url: str):
    audio_queue: asyncio.Queue[bytes | None] = asyncio.Queue()
    playback_queue: asyncio.Queue[bytes] = asyncio.Queue()
    settings = get_settings()

    async def on_status(msg: str):
        print(f"[{mid}] {msg}")
        state = meetings[mid]["state"]
        lower = msg.lower()
        if "waiting" in lower or "admitted" in lower:
            state = "waiting"
        elif "audio capture active" in lower or "live" in lower:
            state = "live"
        elif "error" in lower:
            state = "error"
        meetings[mid]["status"] = msg
        meetings[mid]["state"] = state
        await broadcast({"type": "status", "meeting_id": mid, "message": msg, "state": state})

    async def on_audio(pcm_bytes: bytes):
        await audio_queue.put(pcm_bytes)

    async def on_transcript(text: str):
        print(f"[{mid}] transcript: {text}")
        meetings[mid]["transcript"].append(text)
        await broadcast({"type": "transcript", "meeting_id": mid, "text": text})

    async def on_assistant(text: str):
        print(f"[{mid}] assistant: {text}")
        meetings[mid]["actions"].append(text)
        await broadcast({"type": "assistant", "meeting_id": mid, "text": text})

    async def on_extraction(extraction):
        data = extraction.model_dump()
        meetings[mid]["extraction"] = data
        await broadcast({"type": "extraction", "meeting_id": mid, "extraction": data})

    def on_session(session):
        # Hold the live session so the run-tasks endpoint can drive the batch
        # runner (context + mark-done) against the same state the gate mutates.
        meetings[mid]["session"] = session

    from pipeline_bridge import run_meet_pipeline

    meet_task = asyncio.create_task(
        join_meet(
            meeting_url,
            on_audio,
            on_status,
            bot_name=settings.meeting_bot_name,
            playback_queue=playback_queue,
            playback_sample_rate=settings.meeting_playback_sample_rate,
        )
    )
    pipeline_task = asyncio.create_task(
        run_meet_pipeline(
            audio_queue,
            playback_queue,
            on_transcript,
            on_assistant,
            on_extraction,
            on_session,
        )
    )

    try:
        # Wait for the first task to finish or raise; cancel the other so we never
        # leak an orphaned Playwright process or a pipeline blocked on audio_queue.
        done, pending = await asyncio.wait(
            [meet_task, pipeline_task], return_when=asyncio.FIRST_COMPLETED
        )
        for t in pending:
            t.cancel()
            try:
                await t
            except (asyncio.CancelledError, Exception):
                pass
        # Re-raise any exception from the finished task(s)
        for t in done:
            t.result()
    except Exception as e:
        print(f"[{mid}] error: {e}")
        meetings[mid]["state"] = "error"
        meetings[mid]["status"] = str(e)
        await broadcast({"type": "status", "meeting_id": mid, "message": str(e), "state": "error"})
    finally:
        await audio_queue.put(None)
        if meetings[mid]["state"] not in ("error",):
            meetings[mid]["state"] = "ended"
            await broadcast(
                {"type": "status", "meeting_id": mid, "message": "Meeting ended", "state": "ended"}
            )


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("main:app", host="0.0.0.0", port=8000, reload=False)
