# Carleton — your meeting never forgets!

You know the feeling. You walk out of a two-hour call, open a blank doc, and realize you can only remember the last three things said. Everything else is gone, the decisions, the tasks someone casually agreed to, the little detail that was actually the whole point. So you either spend 30 minutes reconstructing the meeting from memory or you just let it slip. 

Carleton sits in the call with you. It listens the whole time, builds a live understanding of what's happening, and most importantly, ACTS on it: filing tickets, sending emails, drafting a doc - the moment someone asks or after the meeting end, right there in the meeting. 

**Video demo:** TBA

---

## How it works

Carleton joins your Google Meet as a guest via Playwright + Chrome, captures the mixed audio stream, and runs it through a Pipecat voice pipeline:

```
Meet audio => VAD => STT => WakeNameGate => TTS => back into the call
```

The pipeline is deliberately passive. Carleton never interrupts and only responds when called by name. It just listens, accumulates transcript lines, and runs a rolling extraction every few minutes in the background (a lightweight Claude Haiku call that turns the lines into a structured working artifact)

- **context** — a tight running prose summary of what's been decided and discussed
- **open_tasks** — concrete, addressable things someone asked for ("create a ticket for the login bug")
- **preference_candidates** — durable team practices worth remembering across meetings

When someone says "hey Carleton, file that ticket" or "Carleton, email the deck to Sam", Carleton catches it. Then it dispatches to the meeting brain, a Claude Agent SDK agent with MCP tools for Jira, Slack, Gmail, Linear, and Google Drive. It acts immediately and replies in one spoken sentence. At the end of the meeting you can also hit "run tasks" to execute everything Carleton noted but nobody verbally triggered. 

The rolling extraction means the brain does not need to read the whole transcript too. It reads `summary + pending tasks + last N lines` which is short, cheap, low-latency. Cost scales with new speech per interval instead of meeting length.

Team preferences get written to Obsidian across meetings, so Carleton also gradually learns how your team works.

---

## Using Pipecat, Nemotron, and Cekura

### Pipecat
The entire voice path is built on Pipecat. VAD (Silero), STT (Deepgram or NVIDIA Parakeet), TTS (Cartesia), and the frame pipeline are all Pipecat. The `WakeNameGate` is a custom `FrameProcessor` that sits between STT and TTS — it drops interim frames, filters self-echoes (the bot re-captures its own TTS through the mixed Meet stream), and dispatches addressed requests off the voice path so the pipeline never blocks.

### Cekura
We used Cekura to evaluate whether Carleton's task extraction was actually catching the right things. The stress test (`tests/stress_cekura.py`) spins up 10 concurrent synthetic meeting sessions with distinct contexts and task sets, builds scorecards, and submits them to Cekura concurrently — the goal being to measure extraction accuracy, catch variance in what gets flagged as a "task" vs. casual chatter, and stress-test the eval pipeline itself before relying on it for real meetings.

---

## What's new in the hackathon

Carleton was built during the hackathon:

- The passive meeting listener (Pipecat pipeline with no conversational LLM, wake-word gate)
- Rolling extraction — the incremental Haiku extraction loop with snapshot-then-apply semantics so lines can't be dropped mid-pass
- Meeting brain with MCP tool dispatch (Jira, Slack, Gmail, Linear, Google Drive, dynamic runtime-added servers)
- Batch task runner — the same executor the wake word uses, triggered at meeting end
- Long-term Obsidian memory (team preferences promoted across meetings)
- The Carleton dashboard (Next.js, WebSocket, per-meeting transcript/tasks/activity)
- Cekura extraction accuracy evaluation

---

## Feedback

**Pipecat** — the frame model is clean and the composability is real. The one rough edge: there's no first-class way to run a side-effect off a frame without either blocking the pipeline or spinning a raw `asyncio.create_task` and manually tracking it for teardown. For the wake-word dispatch and the rolling extractor we ended up doing the latter, which works but is easy to get wrong (leaking tasks into a dead pipeline). A `FrameProcessor.spawn_task()` that Pipecat tracks and cancels at cleanup would be a clean primitive.

**Cekura** — the eval loop concept is solid. The main friction was knowing what to put in the scorecard vs. the session state to get actionable signal back. More example scorecards for voice/multi-turn agents would shorten the ramp-up. One potential bug: concurrent submissions with very similar transcripts sometimes returned swapped verdict labels, so it might be worth investigating whether the eval pipeline has a race on the session context.
