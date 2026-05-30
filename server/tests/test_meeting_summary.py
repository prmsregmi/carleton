"""Rolling-summary memory for the meeting agent.

Covers the two pieces with real logic: the session's tail/summary bookkeeping
(snapshot-then-clear semantics that must not drop lines arriving mid-summarize)
and the keyless deterministic summary stub used by the offline pipeline.
"""

from app.meeting.session import MeetingSessionState
from app.meeting.summarizer import _stub_summary, summarize


# --- session: tail accumulation + brain context ---
def test_add_line_accumulates_in_tail():
    s = MeetingSessionState()
    s.add_line("alice: hi")
    s.add_line("bob: hey")
    text, count = s.take_unsummarized()
    assert count == 2
    assert text == "alice: hi\nbob: hey"


def test_context_for_brain_composes_summary_then_tail():
    s = MeetingSessionState()
    s.running_summary = "We discussed the Q3 launch."
    s.add_line("alice: what's the date again?")
    ctx = s.context_for_brain()
    assert "We discussed the Q3 launch." in ctx
    assert "alice: what's the date again?" in ctx
    # Summary precedes the fresh tail so the model reads old-to-new.
    assert ctx.index("Q3 launch") < ctx.index("what's the date")


def test_context_for_brain_summary_only_when_tail_empty():
    s = MeetingSessionState()
    s.running_summary = "Prior context."
    assert "Prior context." in s.context_for_brain()


def test_context_for_brain_empty_when_nothing_seen():
    assert MeetingSessionState().context_for_brain() == ""


# --- session: snapshot-then-clear must not lose mid-summarize lines ---
def test_apply_summary_sets_summary_and_clears_consumed():
    s = MeetingSessionState()
    s.add_line("a")
    s.add_line("b")
    _, count = s.take_unsummarized()
    s.apply_summary("summary of a,b", count)
    assert s.running_summary == "summary of a,b"
    text, remaining = s.take_unsummarized()
    assert remaining == 0
    assert text == ""


def test_apply_summary_preserves_lines_added_during_summarization():
    s = MeetingSessionState()
    s.add_line("a")
    s.add_line("b")
    _, snapshot = s.take_unsummarized()  # snapshot == 2
    # A new line lands while the (async) summarizer is still running.
    s.add_line("c")
    s.apply_summary("summary of a,b", snapshot)
    text, remaining = s.take_unsummarized()
    assert remaining == 1
    assert text == "c"  # the in-flight line survived, not dropped


# --- keyless deterministic stub (offline pipeline) ---
def test_stub_summary_seeds_from_new_lines_when_no_prior():
    assert _stub_summary("alice: hi\nbob: hey", "") == "- alice: hi | bob: hey"


def test_stub_summary_carries_previous_summary_forward():
    prev = "- alice: hi | bob: hey"
    assert _stub_summary("carol: ok", prev) == "- alice: hi | bob: hey\n- carol: ok"


def test_stub_summary_returns_prev_unchanged_when_no_new_lines():
    assert _stub_summary("", "prev") == "prev"


async def test_summarize_routes_to_stub_without_api_key():
    class _NoKey:
        anthropic_api_key = None
        meeting_summary_model = "claude-haiku-4-5"

    out = await summarize("alice: hi", "", settings=_NoKey())
    assert out == "- alice: hi"


async def test_summarize_returns_none_on_agent_error(monkeypatch):
    """A failed summary must signal failure (None) so the loop keeps the lines
    for retry instead of consuming them into an unchanged summary."""
    import app.meeting.summarizer as sm

    async def boom(*args, **kwargs):
        raise RuntimeError("api down")

    monkeypatch.setattr(sm, "_agent_summary", boom)

    class _Key:
        anthropic_api_key = "sk-test"
        meeting_summary_model = "claude-haiku-4-5"

    assert await sm.summarize("alice: hi", "prev", settings=_Key()) is None


# --- tail cap is a backstop that drops OLDEST, never the in-flight snapshot ---
def test_tail_is_unbounded_during_flight_so_snapshot_offset_stays_valid():
    # A summarize pass snapshots N lines; many more arrive before apply. None of
    # the snapshotted lines may be evicted, or apply_summary would pop the wrong ones.
    s = MeetingSessionState(tail_cap=3)
    s.add_line("a")
    s.add_line("b")
    _, snapshot = s.take_unsummarized()  # snapshot == 2 (a, b)
    for line in ["c", "d", "e", "f"]:  # flood well past the cap mid-flight
        s.add_line(line)
    s.apply_summary("summary of a,b", snapshot)
    text, _ = s.take_unsummarized()
    # a,b consumed; the cap then trims oldest of what remains, keeping the NEWEST.
    assert "a" not in text and "b" not in text
    assert text.endswith("f")


def test_apply_summary_caps_backlog_dropping_oldest():
    s = MeetingSessionState(tail_cap=3)
    for line in ["a", "b", "c", "d", "e"]:
        s.add_line(line)
    s.apply_summary("sum", 0)  # nothing consumed, but 5 > cap 3
    text, count = s.take_unsummarized()
    assert count == 3
    assert text == "c\nd\ne"  # newest three kept, oldest two dropped
