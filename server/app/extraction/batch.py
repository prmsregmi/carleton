"""Batch execution of the rolling extraction's pending tasks.

The passive path — "do all remaining tasks" at meeting end, from the dashboard
button or a chatbot field — runs each pending task through the SAME interface the
wake word uses: `execute(task_text, context)`, which in production is
`app.meeting.brain.handle_request`. No duplicate executor; only the context and
the trigger differ.
"""

from collections.abc import Awaitable, Callable

from loguru import logger

Executor = Callable[[str, str], Awaitable[str]]
OnResult = Callable[[str, str], Awaitable[None]]


async def run_pending_tasks(
    session, execute: Executor, *, on_result: OnResult | None = None
) -> list[tuple[str, str]]:
    """Execute every pending task in `session.extraction` through `execute`, mark
    each done on success (left pending on failure so it can be retried), and return
    the (task_text, result) pairs. `on_result` fires per task for live broadcast.

    The pending set is snapshotted up front so a concurrent extraction tick adding
    new tasks doesn't extend this run."""
    pending = [t for t in session.extraction.open_tasks if t.status == "pending"]
    results: list[tuple[str, str]] = []
    for task in pending:
        context = session.context_for_brain()
        try:
            result = await execute(task.text, context)
            session.mark_task_done(task.text)
        except Exception as exc:  # one bad task must not abort the batch
            logger.warning(f"batch task failed, left pending: {exc!r}")
            result = f"Failed: {exc}"
        if on_result is not None:
            await on_result(task.text, result)
        results.append((task.text, result))
    return results
