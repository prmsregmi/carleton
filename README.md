# ginjoly

A voice agent that picks up the phone, runs a structured screening interview, and quietly fact-checks
what you tell it while you're still talking.

You call in. It asks who you are, walks through a set of interview questions, and as you make claims
("I built X", "I work at Y", "here's my GitHub") it spins up a separate research agent off the voice path
to corroborate them against public sources. When you hang up, it writes a scorecard and ships the
transcript to an eval platform. Nobody waited on hold while a model thought.

Built for the YC / Cekura / Daily voice-agents hackathon, on [Pipecat](https://pipecat.ai).

---

## What it actually does

- **Talks in real time.** A streaming cascade — speech-to-text → LLM → text-to-speech — so it answers
  mid-thought instead of after a long pause.
- **Runs a script, not a vibe.** A [pipecat-flows](https://docs.pipecat.ai/server/frameworks/flows/pipecat-flows)
  state graph drives the call through three nodes: `collect_anchors → questioning → close`. It collects
  identity anchors (name, company, email), asks a context-specific question bank, and closes cleanly.
- **Fact-checks off the voice path.** When you make a claim, the agent fires a background
  [Claude Agent SDK](https://docs.claude.com/en/api/agent-sdk) researcher that searches Google + LinkedIn
  (via ScrapingDog) and GitHub, scores how well the public record matches your anchors, and returns one of
  three verdicts: **corroborated**, **contradicted**, or **unconfirmed**. The voice loop never blocks on it.
- **Scores the call.** At hangup it writes a `Scorecard` (anchors, claims, verdicts, latencies, completion)
  and submits the transcript to [Cekura](https://cekura.com) for evaluation.
- **Swaps its brain and ears without surgery.** STT and LLM are selectable by env var. Run the
  Deepgram + Claude baseline, or switch to NVIDIA's open **Nemotron** stack (Speech Streaming STT +
  Nemotron-3-Super via vLLM) — same flow graph, same tools, one variable changed.

> It returns a *corroboration verdict against public data*, not proof of identity. It's built to never
> assert ownership it can't support.

---

## How it works

```
  caller ─▶ transport ─▶ STT ─▶ context+LLM ─▶ TTS ─▶ transport ─▶ caller
                                    │
                                    ├─ flow graph: collect_anchors → questioning → close
                                    │
                                    └─ verify_claim()  ──▶  background research agent
                                                            (ScrapingDog · LinkedIn · GitHub)
                                                                     │
                                                                     ▼
                                                              verdict ─▶ Scorecard ─▶ Cekura
```

The pipeline is assembled in [`server/bot.py`](server/bot.py). Everything streams: the STT emits interim
transcripts, the LLM streams tokens, and the TTS starts speaking on the first clause — so end-to-end
latency is the sum of each stage's *time-to-first-byte*, overlapped, not the sum of full responses.

**Turn-taking** uses smart-turn endpointing (`FilterIncompleteUserTurnStrategies`) instead of cutting you
off on the first silence. With the Nemotron STT, the recognizer finalizes the turn itself the instant VAD
detects end-of-speech, so the agent jumps in fast without talking over you.

**System prompts** live on the flow nodes (`role_message`), not on the LLM — so swapping the model never
touches the conversation logic.

### Selectable stacks

| Stage | Baseline (default) | NVIDIA open-model stack |
|-------|--------------------|--------------------------|
| STT   | Deepgram           | Nemotron Speech Streaming (websocket) |
| LLM   | Anthropic Claude   | Nemotron-3-Super-120B via vLLM (OpenAI-compatible) |
| TTS   | Cartesia           | Cartesia |

Switch with two env vars:

```bash
STT_PROVIDER=nemotron LLM_PROVIDER=nemotron uv run bot.py -t webrtc
```

The Nemotron endpoints default to the hackathon's hosted AWS fleet and need no key. Point them at your own
vLLM / ASR deployment by overriding `NVIDIA_ASR_URL` and `NEMOTRON_LLM_URL` — no code change.

---

## Setup

### Prerequisites

- Python 3.11+ and [`uv`](https://docs.astral.sh/uv/)
- Node (for the optional browser client)
- API keys (below)

### Keys

Copy the template and fill it in:

```bash
cd server
cp .env.example .env
```

| Variable | Needed for | Notes |
|----------|-----------|-------|
| `DEEPGRAM_API_KEY`   | STT (baseline) | speech-in |
| `ANTHROPIC_API_KEY`  | LLM + research agent | also powers verification |
| `CARTESIA_API_KEY`   | TTS | speech-out |
| `SCRAPINGDOG_API_KEY`| verification | Google + LinkedIn lookups (degrades to a stub without it) |
| `GITHUB_TOKEN`       | verification | optional; unauthenticated works but is rate-limited |
| `CEKURA_API_KEY`     | eval | optional; transcript submission is best-effort |
| `DAILY_API_KEY`      | hosted transport | only for `-t daily`, not for local `-t webrtc` |

The Nemotron stack needs **no key** — it's URL-driven.

Minimum to hold a conversation: `DEEPGRAM_API_KEY`, `ANTHROPIC_API_KEY`, `CARTESIA_API_KEY`.

### Run it locally

```bash
cd server
uv sync
uv run bot.py -t webrtc      # keyless peer-to-peer transport, no Daily account
```

Open **http://localhost:7860**, click **Connect**, allow your mic, and talk. First launch downloads the
VAD + smart-turn models (~20s), then the agent greets you and starts the interview.

Prefer the project's own client? In a second terminal:

```bash
cd client && npm install && npm run dev   # http://localhost:5173
```

---

## Deploy

ginjoly ships a `Dockerfile` (on `dailyco/pipecat-base`) and a `pcc-deploy.toml`, so it deploys to
[Pipecat Cloud](https://docs.pipecat.ai/deployment/pipecat-cloud/introduction) as a managed agent:

```bash
uv tool install pipecat-ai-cli
pc cloud auth login
pc cloud secrets set ginjoly-secrets --file server/.env
pc cloud deploy
```

Pipecat Cloud runs your container and auto-provisions the Daily room/transport, so the same `bot.py` that
runs on your laptop runs in the cloud — your machine is never in the call path.

---

## Meet Bot (Aria)

Aria sends a bot into a Google Meet. A Playwright bot joins as a guest, captures the meeting's mixed
audio, and feeds it to ginjoly's **meeting agent** — which listens passively and, when addressed by its
wake name, runs the request against external MCP tools (Jira / Slack / Gmail) and reports back. Live
transcript and the bot's replies stream to the web UI.

The `connector/` is only the on-ramp: `connector/bridge` (Python) captures and streams audio, and
`connector/web` (Next.js) is the UI. All voice and agent logic lives in `server/`. (Speaking back *into*
the meeting isn't wired yet — replies appear in the UI.)

### How to run

**1. Keys** — the bridge runs under the `server` venv and reads `server/.env`, which already has
`DEEPGRAM_API_KEY` / `CARTESIA_API_KEY` / `ANTHROPIC_API_KEY`. To enable real actions, also add:

```bash
# server/.env
MEETING_WAKE_NAMES=onion
JIRA_MCP_URL=...    JIRA_MCP_TOKEN=...     # same shape for SLACK_ and GMAIL_
```

Without the MCP keys the bot still listens and replies, but says it has no tools connected.

**2. Start the bridge** (Python capture + agent; needs Google Chrome installed):

```bash
cd server
uv run python ../connector/bridge/main.py     # http://localhost:8000
```

**3. Start the web UI:**

```bash
cd connector/web
npm install
npm run dev                                    # http://localhost:3000
```

**4. Open [http://localhost:3000](http://localhost:3000)** — paste a Google Meet link → click **Join
meeting** → a Chrome window opens and the bot joins as **"Onion"** → admit it from Meet's waiting
room → transcript and replies appear live.

---

## Project layout

```
ginjoly/
├── server/                      # the agent logic — two agents on one cascade pipeline
│   ├── bot.py                   # interview entry (pipecat runner: webrtc | daily)
│   ├── app/
│   │   ├── config.py            # typed settings / env (shared)
│   │   ├── llm_factory.py       # selectable LLM (anthropic | nemotron | openai | nim)
│   │   ├── stt_factory.py       # selectable STT (deepgram | nemotron)
│   │   ├── services/            # Nemotron STT + vLLM LLM adapters
│   │   ├── interview/           # interview agent: flow graph, contexts, verify, scorecard
│   │   └── meeting/             # meeting agent: wake-word gate, MCP brain, pipeline
│   ├── Dockerfile               # Pipecat Cloud image
│   └── pcc-deploy.toml          # Pipecat Cloud deploy config
├── connector/                   # joins a Google Meet, feeds audio to server/, shows the UI
│   ├── bridge/                  # Python: FastAPI + Playwright
│   │   ├── main.py              # FastAPI: POST /api/join, WebSocket /ws (status/transcript/replies)
│   │   ├── pipeline_bridge.py   # runs server/'s meeting agent over the captured Meet audio
│   │   ├── agent/meet_bot.py    # Playwright bot that joins Google Meet as a guest
│   │   └── static/audio_worklet.js  # captures mixed audio inside the Meet page
│   └── web/                     # Next.js UI: paste link → live transcript + replies + actions
├── client/                      # original browser client (Vite)
├── LICENSE                      # Apache-2.0
└── README.md
```

### Screening contexts

The interview is parameterized. `HACKATHON_APPLICANT` ships fully built (5 technical questions + a scoring
rubric); `JOB_PHONE_SCREEN` and `EVENT_LEAD` are defined as types and ready to be filled in. A context
declares its own anchors, question bank, rubric, intro/close scripts, and corroboration threshold — so
pointing the agent at a new screening is data, not code.

---

## License

[Apache-2.0](LICENSE).
