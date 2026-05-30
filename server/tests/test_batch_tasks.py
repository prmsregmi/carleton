"""End-of-meeting batch execution of pending tasks.

The passive path ("do all tasks") must run through the SAME execution interface
as the wake word — `handle_request(text, context)` — so there is no duplicate
executor. `run_pending_tasks` is the thin driver over that interface.
"""

from app.extraction.batch import run_pending_tasks
from app.extraction.schema import RollingExtraction, Task
from app.meeting.session import MeetingSessionState


async def test_run_pending_tasks_executes_each_pending_and_marks_done():
    s = MeetingSessionState()
    s.extraction = RollingExtraction(
        open_tasks=[Task(text="t1"), Task(text="t2", status="done"), Task(text="t3")]
    )
    calls = []

    async def execute(text, context):
        calls.append(text)
        return f"did {text}"

    results = await run_pending_tasks(s, execute)
    # The already-done task is skipped; the two pending ones run in order.
    assert calls == ["t1", "t3"]
    assert all(t.status == "done" for t in s.extraction.open_tasks)
    assert results == [("t1", "did t1"), ("t3", "did t3")]


async def test_run_pending_tasks_keeps_task_pending_on_failure():
    s = MeetingSessionState()
    s.extraction = RollingExtraction(open_tasks=[Task(text="boom")])

    async def execute(text, context):
        raise RuntimeError("mcp down")

    results = await run_pending_tasks(s, execute)
    # A failed task is not marked done, so it can be retried.
    assert s.extraction.open_tasks[0].status == "pending"
    assert "mcp down" in results[0][1]


async def test_run_pending_tasks_passes_brain_context_to_executor():
    s = MeetingSessionState()
    s.extraction = RollingExtraction(context="prior context", open_tasks=[Task(text="t1")])
    seen = {}

    async def execute(text, context):
        seen["context"] = context
        return "ok"

    await run_pending_tasks(s, execute)
    assert "prior context" in seen["context"]


async def test_run_pending_tasks_fires_on_result_callback():
    s = MeetingSessionState()
    s.extraction = RollingExtraction(open_tasks=[Task(text="t1")])
    notified = []

    async def execute(text, context):
        return "done it"

    async def on_result(text, result):
        notified.append((text, result))

    await run_pending_tasks(s, execute, on_result=on_result)
    assert notified == [("t1", "done it")]
