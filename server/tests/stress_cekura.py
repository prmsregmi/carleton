"""
Cekura stress test — 10 concurrent synthetic interview sessions.

Each agent runs a full SessionState lifecycle with fabricated anchors, claims,
and a realistic multi-turn transcript, then submits to Cekura via submit_transcript.

Run from the server/ directory:
    uv run python tests/stress_cekura.py

Requires CEKURA_API_KEY and CEKURA_AGENT_ID in server/.env (or env).
Set DRY_RUN=1 to skip actual HTTP and just print what would be sent.
"""

import asyncio
import json
import os
import sys
import time
import uuid
from datetime import UTC, datetime

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from dotenv import load_dotenv

load_dotenv(dotenv_path=os.path.join(os.path.dirname(__file__), "..", ".env"), override=True)

from loguru import logger

from app.interview.anchors import CallerAnchors
from app.interview.contexts.hackathon import HACKATHON_CONTEXT
from app.interview.scorecard.cekura import submit_transcript
from app.interview.scorecard.schema import ClaimRecord, LatencyMetrics, Scorecard
from app.interview.session import SessionState
from app.interview.verify.schema import Evidence, Verdict, VerdictLabel

DRY_RUN = os.getenv("DRY_RUN", "0") == "1"
NUM_AGENTS = 10

# ---------------------------------------------------------------------------
# Synthetic data pools
# ---------------------------------------------------------------------------

FAKE_APPLICANTS = [
    {
        "name": f"Applicant {i + 1}",
        "company": company,
        "email": f"applicant{i + 1}@example.com",
        "profile_url": f"https://github.com/applicant{i + 1}",
    }
    for i, company in enumerate(
        [
            "MIT",
            "Stanford",
            "CMU",
            "YC S24",
            "Stripe",
            "OpenAI",
            "Anthropic",
            "Figma",
            "Linear",
            "Vercel",
        ]
    )
]

FAKE_CLAIMS = [
    ("Built a distributed key-value store in Rust", VerdictLabel.CORROBORATED),
    ("Led the ML infra team at a Series B startup", VerdictLabel.UNCONFIRMED),
    ("Contributed 200+ commits to CPython", VerdictLabel.CONTRADICTED),
    ("Shipped an iOS app with 50k DAU", VerdictLabel.UNCONFIRMED),
    ("Core contributor to the React compiler", VerdictLabel.CORROBORATED),
    ("Wrote a compiler backend in LLVM", VerdictLabel.UNCONFIRMED),
    ("Won HackMIT 2023", VerdictLabel.CORROBORATED),
    ("Built a real-time multiplayer game engine", VerdictLabel.UNCONFIRMED),
    ("Co-authored a paper at NeurIPS", VerdictLabel.CONTRADICTED),
    ("Maintained a popular open-source ORM", VerdictLabel.CORROBORATED),
]

FAKE_QA_TURNS = [
    ("assistant", "Hi, thanks for calling in. Could you tell me your name?"),
    ("user", "Sure, it's {name}."),
    ("assistant", "Great. Where do you currently work or study?"),
    ("user", "I'm at {company}."),
    ("assistant", "What's the most technically demanding thing you've built?"),
    ("user", "{claim}"),
    ("assistant", "Interesting — what was the hardest sub-problem you ran into?"),
    ("user", "Probably the consensus layer. Keeping nodes in sync under partition was brutal."),
    ("assistant", "How did you solve it?"),
    ("user", "We ended up implementing a simplified Raft with a custom leader election timeout."),
    ("assistant", "Is it live or used by anyone?"),
    ("user", "Yeah, it's open source. You can see it on my GitHub."),
    ("assistant", "Thanks. That's everything I needed. We'll be in touch!"),
]


def make_transcript(applicant: dict, claim: str) -> list[dict]:
    return [
        {
            "role": role,
            "content": msg.format(
                name=applicant["name"],
                company=applicant["company"],
                claim=claim,
            ),
        }
        for role, msg in FAKE_QA_TURNS
    ]


# ---------------------------------------------------------------------------
# Single-agent simulation
# ---------------------------------------------------------------------------


async def run_agent(index: int) -> dict:
    applicant = FAKE_APPLICANTS[index]
    claim_text, verdict_label = FAKE_CLAIMS[index]
    t0 = time.perf_counter()

    session = SessionState(HACKATHON_CONTEXT)

    # Populate anchors
    session.set_anchors(**applicant)

    # Record a claim
    verdict = Verdict(
        label=verdict_label,
        confidence=0.85,
        evidence=[
            Evidence(
                source="github",
                url="https://github.com/applicant{}".format(index + 1),
                snippet="Synthetic stress-test evidence",
            )
        ],
        reasoning="Synthetic stress-test verdict",
    )
    session.record_claim(
        claim=claim_text,
        verdict=verdict,
        question_id="q_project",
        latency_ms=round(120 + index * 13.7, 1),
    )

    session.questions_asked = 5
    session.completed = True

    transcript = make_transcript(applicant, claim_text)

    scorecard = session.to_scorecard()

    elapsed_build = (time.perf_counter() - t0) * 1000

    if DRY_RUN:
        logger.info(
            f"[agent {index:02d}] DRY RUN — call_id={session.call_id} "
            f"anchors={applicant['name']} claim={verdict_label.value}"
        )
        submitted = False
    else:
        submitted = await submit_transcript(
            call_id=session.call_id,
            prompt_version=session.prompt_version,
            turns=transcript,
        )

    elapsed_total = (time.perf_counter() - t0) * 1000

    return {
        "index": index,
        "call_id": session.call_id,
        "name": applicant["name"],
        "company": applicant["company"],
        "verdict": verdict_label.value,
        "submitted": submitted,
        "build_ms": round(elapsed_build, 1),
        "total_ms": round(elapsed_total, 1),
        "scorecard": scorecard.model_dump(mode="json"),
    }


# ---------------------------------------------------------------------------
# Runner
# ---------------------------------------------------------------------------


async def main() -> None:
    logger.info(f"Starting Cekura stress test: {NUM_AGENTS} concurrent agents (DRY_RUN={DRY_RUN})")
    wall_start = time.perf_counter()

    results = await asyncio.gather(*[run_agent(i) for i in range(NUM_AGENTS)])

    wall_ms = (time.perf_counter() - wall_start) * 1000

    ok = [r for r in results if r["submitted"] or DRY_RUN]
    fail = [r for r in results if not r["submitted"] and not DRY_RUN]

    print("\n" + "=" * 64)
    print(f"  Cekura stress test — {NUM_AGENTS} agents")
    print("=" * 64)
    print(f"  Wall time:   {wall_ms:.0f} ms")
    print(f"  Submitted:   {len(ok)}/{NUM_AGENTS}")
    print(f"  Failed:      {len(fail)}")
    print()

    for r in results:
        status = "✓" if (r["submitted"] or DRY_RUN) else "✗"
        print(
            f"  {status} [{r['index']:02d}] {r['name']:<20} "
            f"{r['verdict']:<15} call={r['call_id']}  "
            f"total={r['total_ms']:.0f}ms"
        )

    if fail:
        print(f"\n  {len(fail)} submission(s) failed — check CEKURA_API_KEY / CEKURA_AGENT_ID")

    print()

    # Write results JSON for inspection
    out_path = os.path.join(os.path.dirname(__file__), "stress_results.json")
    with open(out_path, "w") as f:
        json.dump(
            {
                "run_at": datetime.now(UTC).isoformat(),
                "num_agents": NUM_AGENTS,
                "dry_run": DRY_RUN,
                "wall_ms": round(wall_ms, 1),
                "submitted_count": len(ok),
                "failed_count": len(fail),
                "agents": results,
            },
            f,
            indent=2,
            default=str,
        )
    logger.info(f"Results written to {out_path}")


if __name__ == "__main__":
    asyncio.run(main())
