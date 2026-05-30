"""Rolling-extraction memory for the meeting agent.

Covers the session's tail/extraction bookkeeping (snapshot-then-clear semantics
that must not drop lines arriving mid-extract, plus the task-done marking the
wake word and the batch runner share) and the keyless deterministic extraction
stub used by the offline pipeline.
"""

from app.extraction.rolling import _stub_extraction, extract
from app.extraction.schema import RollingExtraction, Task
from app.meeting.session import MeetingSessionState


# --- session: tail accumulation ---
def test_add_line_accumulates_in_tail():
    s = MeetingSessionState()
    s.add_line("alice: hi")
    s.add_line("bob: hey")
    text, count = s.take_unsummarized()
    assert count == 2
    assert text == "alice: hi\nbob: hey"


def test_full_transcript_retains_lines_after_tail_consumed():
    s = MeetingSessionState()
    s.add_line("alice: hi")
    s.add_line("bob: hey")
    _, count = s.take_unsummarized()
    s.apply_extraction(RollingExtraction(context="sum"), count)  # tail drained
    assert s.take_unsummarized() == ("", 0)
    # The permanent record keeps everything for the archive.
    assert s.full_transcript() == "alice: hi\nbob: hey"


# --- session: brain context composes extraction context + pending tasks + tail ---
def test_context_for_brain_composes_context_then_tail():
    s = MeetingSessionState()
    s.extraction = RollingExtraction(context="We discussed the Q3 launch.")
    s.add_line("alice: what's the date again?")
    ctx = s.context_for_brain()
    assert "We discussed the Q3 launch." in ctx
    assert "alice: what's the date again?" in ctx
    # Context precedes the fresh tail so the model reads old-to-new.
    assert ctx.index("Q3 launch") < ctx.index("what's the date")


def test_context_for_brain_lists_pending_tasks_only():
    s = MeetingSessionState()
    s.extraction = RollingExtraction(
        open_tasks=[Task(text="file a ticket"), Task(text="email bob", status="done")]
    )
    ctx = s.context_for_brain()
    assert "file a ticket" in ctx
    # A completed task is not surfaced as still-open work.
    assert "email bob" not in ctx


def test_context_for_brain_empty_when_nothing_seen():
    assert MeetingSessionState().context_for_brain() == ""


# --- session: snapshot-then-clear must not lose mid-extract lines ---
def test_apply_extraction_sets_extraction_and_clears_consumed():
    s = MeetingSessionState()
    s.add_line("a")
    s.add_line("b")
    _, count = s.take_unsummarized()
    s.apply_extraction(RollingExtraction(context="extraction of a,b"), count)
    assert s.extraction.context == "extraction of a,b"
    text, remaining = s.take_unsummarized()
    assert remaining == 0
    assert text == ""


def test_apply_extraction_preserves_lines_added_during_extract():
    s = MeetingSessionState()
    s.add_line("a")
    s.add_line("b")
    _, snapshot = s.take_unsummarized()  # snapshot == 2
    # A new line lands while the (async) extractor is still running.
    s.add_line("c")
    s.apply_extraction(RollingExtraction(context="extraction of a,b"), snapshot)
    text, remaining = s.take_unsummarized()
    assert remaining == 1
    assert text == "c"  # the in-flight line survived, not dropped


# --- session: tail cap is a backstop that drops OLDEST, never the in-flight snapshot ---
def test_tail_is_unbounded_during_flight_so_snapshot_offset_stays_valid():
    s = MeetingSessionState(tail_cap=3)
    s.add_line("a")
    s.add_line("b")
    _, snapshot = s.take_unsummarized()  # snapshot == 2 (a, b)
    for line in ["c", "d", "e", "f"]:  # flood well past the cap mid-flight
        s.add_line(line)
    s.apply_extraction(RollingExtraction(context="extraction of a,b"), snapshot)
    text, _ = s.take_unsummarized()
    assert "a" not in text and "b" not in text
    assert text.endswith("f")


def test_apply_extraction_caps_backlog_dropping_oldest():
    s = MeetingSessionState(tail_cap=3)
    for line in ["a", "b", "c", "d", "e"]:
        s.add_line(line)
    s.apply_extraction(RollingExtraction(context="sum"), 0)  # nothing consumed, 5 > cap 3
    text, count = s.take_unsummarized()
    assert count == 3
    assert text == "c\nd\ne"  # newest three kept, oldest two dropped


# --- session: task-done marking (shared by wake word + batch runner) ---
def test_mark_task_done_flips_matching_pending_task():
    s = MeetingSessionState()
    s.extraction = RollingExtraction(open_tasks=[Task(text="create a Jira ticket for the bug")])
    assert s.mark_task_done("create a jira ticket for the bug") is True
    assert s.extraction.open_tasks[0].status == "done"


def test_mark_task_done_returns_false_when_no_match():
    s = MeetingSessionState()
    s.extraction = RollingExtraction(open_tasks=[Task(text="email bob")])
    assert s.mark_task_done("buy milk") is False


# --- keyless deterministic stub (offline pipeline) ---
def test_stub_extraction_seeds_context_from_new_lines():
    out = _stub_extraction("alice: hi\nbob: hey", RollingExtraction())
    assert out.context == "- alice: hi | bob: hey"


def test_stub_extraction_carries_context_forward():
    prev = RollingExtraction(context="- alice: hi | bob: hey")
    out = _stub_extraction("carol: ok", prev)
    assert out.context == "- alice: hi | bob: hey\n- carol: ok"


def test_stub_extraction_carries_tasks_and_prefs_forward():
    prev = RollingExtraction(
        open_tasks=[Task(text="file ticket")], preference_candidates=["prefer async"]
    )
    out = _stub_extraction("carol: ok", prev)
    assert [t.text for t in out.open_tasks] == ["file ticket"]
    assert out.preference_candidates == ["prefer async"]


def test_stub_extraction_returns_prev_unchanged_when_no_new_lines():
    prev = RollingExtraction(context="prev")
    assert _stub_extraction("", prev) is prev


async def test_extract_routes_to_stub_without_api_key():
    class _NoKey:
        anthropic_api_key = None
        meeting_summary_model = "claude-haiku-4-5"

    out = await extract("alice: hi", RollingExtraction(), settings=_NoKey())
    assert out.context == "- alice: hi"


async def test_extract_returns_none_on_agent_error(monkeypatch):
    """A failed extraction must signal failure (None) so the loop keeps the lines
    for retry instead of consuming them into an unchanged extraction."""
    import app.extraction.rolling as r

    async def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(r, "_agent_extraction", boom)

    class _Key:
        anthropic_api_key = "sk-test"
        meeting_summary_model = "claude-haiku-4-5"

    assert await r.extract("alice: hi", RollingExtraction(context="prev"), settings=_Key()) is None
