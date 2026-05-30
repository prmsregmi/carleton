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
    }
    asyncio.create_task(run_bot(mid, req.meeting_url))
    return {"meeting_id": mid}


@app.get("/api/meetings")
async def list_meetings():
    return [meeting_snapshot(mid) for mid in meetings]


@app.get("/api/config")
async def get_config():
    return {"bot_name": get_settings().meeting_bot_name}


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

    from pipeline_bridge import run_meet_pipeline

    meet_task = asyncio.create_task(
        join_meet(meeting_url, on_audio, on_status, bot_name=get_settings().meeting_bot_name)
    )
    pipeline_task = asyncio.create_task(run_meet_pipeline(audio_queue, on_transcript, on_assistant))

    try:
        await asyncio.gather(meet_task, pipeline_task)
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
