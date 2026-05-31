# Carleton — your meeting never forgets

You walk out of a two-hour call and can remember maybe the last three things said. The decisions, the tasks people casually agreed to, the one detail that was the whole point — gone. So you either spend 30 minutes rebuilding the meeting from memory, or you let it slip.

**Carleton sits in the call and does the remembering for you.** It listens the entire time, builds a live structured understanding of what's happening, and — the part that matters — **acts on it**: files the ticket, sends the email, drafts the doc. The moment someone asks, or in one batch at the end. And every meeting makes it smarter about how your team works.

**Video demo (< 60s):**

[![Watch the Carleton demo](https://img.youtube.com/vi/XdPGX2aY5UA/hqdefault.jpg)](https://youtu.be/XdPGX2aY5UA)

---

## How it works

Carleton joins your Google Meet as a guest (Playwright + Chrome), captures the mixed audio, and runs it through a Pipecat voice pipeline:

```
Meet audio → VAD → STT → WakeNameGate → TTS → back into the call
```

The design is deliberately **passive**: there is no conversational LLM in the pipeline, so Carleton never rambles, never interrupts, and costs almost nothing while it listens. It only speaks when addressed by name. Two things run off the voice path:

**1. Rolling extraction (background).** Every few minutes, new transcript lines are folded into a structured artifact — `context` (running summary), `open_tasks` (concrete asks like "create a ticket for the login bug"), and `preference_candidates` (durable team practices). The brain then reads `context + open_tasks + last N lines` instead of the whole transcript, so cost scales with new speech, not meeting length.

**2. The meeting brain (on demand).** When someone says *"Carleton, file that ticket,"* the `WakeNameGate` catches it and dispatches to a Claude Agent SDK agent wired to MCP tools — **Jira, Slack, Gmail, Linear, Google Drive** (plus servers you add at runtime). It executes immediately and confirms in one spoken sentence. At meeting end, **"run tasks"** executes everything Carleton noted that nobody verbally triggered, through the same interface.

---

## The part that compounds: it learns your team

A meeting assistant that forgets everything the moment the call ends is just a fancier transcript. Carleton's real value is that **it accumulates**.

Every meeting, the durable signal — how the team works, recurring decisions, who owns what, the practices people keep referring back to, the gaps that keep surfacing — is written into an **Obsidian knowledge vault** as structured Markdown notes. Those notes are `[[wikilinked]]`, so they form a **graph**: meetings, topics, and practices become connected nodes in Obsidian's networking layer rather than isolated logs. The team's working knowledge stops being a pile of transcripts and becomes a navigable map that grows denser with every call.

And it's a closed loop, not a write-only archive. At the start of each meeting Carleton **seeds the brain with what it already knows** about the team, so it walks in already aware of the conventions, the open threads, and the patterns — and gets sharper every session. Today that knowledge sharpens live extraction and task execution; because it lives as a connected graph, the same substrate is what lets Carleton answer questions about how the team operates in the background, between meetings.

That's the bet: the longer Carleton is in your meetings, the more it understands your team — and the less you have to explain.

---

## Pipecat, Nemotron, and Cekura

### Pipecat
The entire voice path is Pipecat: Silero VAD, swappable STT/TTS, and the frame pipeline on `PipelineWorker`/`WorkerRunner`. The `WakeNameGate` is a custom `FrameProcessor` between STT and TTS that drops interim frames, suppresses self-echo (the bot re-hears its own TTS through the mixed Meet stream), and dispatches addressed requests **off** the voice path so the pipeline never blocks. Shipped to production on **Pipecat Cloud** via cloud build (`bot.py` Daily-WebRTC entrypoint + Dockerfile + `pcc-deploy.toml`), running as an auto-scaling agent — scale-to-zero when idle, an instance per session.

### Nemotron (open weights)
Carleton runs NVIDIA open models on the two jobs that happen constantly, where open-weights latency and cost win:
- **Rolling extraction (default):** `nvidia/nemotron-3-super` over a vLLM OpenAI-compatible endpoint, called with a **forced JSON schema** and thinking off. It produces the `context / open_tasks / preference_candidates` artifact on every tick — fast, cheap, structured. Anthropic is a drop-in fallback via one env var.
- **Speech-to-text:** **NVIDIA Parakeet** streaming ASR as a first-class STT provider, with VAD-driven turn finalization.

### Cekura
The hardest correctness problem for a passive, always-on meeting agent isn't answering well — it's **knowing when it's being addressed**. Acting on a mere mention of its name ("we should ask Carleton later") or on crosstalk is worse than missing a request, so we treated it as a measurable property.

We authored a **30-scenario test suite in Cekura**, run through its hosted **Pipecat (WebRTC) simulation** against the deployed agent. Each scenario pairs a distinct persona and speaking style with an intent and an expected outcome across four dimensions:

1. **Addressed requests** — one per tool, verifying the gate fires and the brain picks the right tool.
2. **False-trigger traps** — the name spoken *about* the agent, not *to* it; it must stay silent.
3. **Underspecified requests** — missing a required field; it must ask for exactly what it needs.
4. **Impossible requests** — unsupported actions; it must decline plainly.

Cekura drives each conversation end-to-end and scores the transcripts, giving per-dimension signal instead of anecdotes. The suite targets the failure mode that matters most — the agent reacting to its name mid-sentence — and drove the hardening of the `WakeNameGate`: leading-filler tolerance, strict first-token addressing so mid-sentence mentions can't trigger, and self-echo suppression. Because the scenarios are defined once and replayed on demand, it's a repeatable regression suite rather than a one-time check.

---

## What's new in the hackathon

Carleton was built during the hackathon:

- The passive meeting listener — Pipecat pipeline with no conversational LLM and the wake-word gate
- Rolling Nemotron extraction with snapshot-then-apply semantics so lines are never dropped mid-pass
- The meeting brain with MCP tool dispatch (Jira, Slack, Gmail, Linear, Google Drive, plus runtime-added servers)
- The end-of-meeting batch runner — the same executor the wake word uses
- **The compounding knowledge layer** — durable team knowledge written to a `[[wikilinked]]` Obsidian graph and seeded back into every future meeting
- The Carleton dashboard (Next.js + WebSocket: live transcript, tasks, and activity)
- The 30-scenario Cekura evaluation suite and the gate hardening it drove
- Pipecat Cloud deployment (Daily-WebRTC entrypoint, cloud build)

Built on top of Pipecat, Pipecat Cloud, the provider SDKs (NVIDIA, Deepgram, Cartesia, Anthropic), and the MCP servers.

---

## Feedback on the tools

**Pipecat** — the frame model is clean and genuinely composable. The one rough edge: there's no first-class way to run a side-effect off a frame. Both the wake-word dispatch and the rolling extractor need to fire async work without blocking the pipeline, and the only option is a raw `asyncio.create_task` you must track and cancel yourself at teardown. We built that bookkeeping; a tracked `FrameProcessor.spawn_task()` that the framework cancels at cleanup would make it safe by default.

**Nemotron** — `nemotron-3-super` is a strong fit for high-frequency structured extraction: low latency with thinking off and reliable schema-constrained JSON for short contexts, at a cost that makes a per-few-minutes loop practical. Parakeet streaming ASR integrated cleanly. Where it could improve: JSON adherence drifts on longer, messier inputs and benefits from strict schema re-prompting, and the ASR's turn-finalization semantics took iteration to align with VAD stop events.

**Cekura** — the hosted Pipecat integration was the highlight: point it at a deployed agent and it runs persona scenarios with zero glue, and auto-generating scenarios from a description is a fast on-ramp. Three things would make it stronger for agents like ours:
- **Multi-speaker simulation.** It's single-caller / two-party, so it can't reproduce concurrent crosstalk — precisely the toughest false-trigger case for a meeting listener.
- **Consistent agent identifiers.** The `PipecatTracer` SDK takes an integer `agent_id` while the observe REST path uses a string — easy to trip over.
- **Tracing for LLM-less pipelines.** The `PipecatTracer` assumes an in-pipeline `LLMContext`; pipelines that run the agent off-path (like ours) have none, so tool-call observability doesn't fit. A documented pattern for this shape would help.

---

## Running it

```bash
# Backend bridge (FastAPI + Playwright + the voice pipeline):
cd connector/bridge
uv run --project ../../server --env-file ../../server/.env python main.py   # :8000

# Dashboard:
cd connector/web && npm run dev                                              # :3000

# Deploy the agent to Pipecat Cloud:
cd server
pc cloud auth login
pc cloud secrets set carleton-secrets --file .env.secrets
pc cloud deploy
```
