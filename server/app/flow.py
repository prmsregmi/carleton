"""Conversation flow: collect_anchors -> questioning -> close.

The plan's separate "intro" stage is folded into the initial collect_anchors
node. The greeting is spoken from bot.py *after* flow_manager.initialize()
completes, not as a node pre-action: a pre-action greeting runs inside
_set_node before the system prompt and tools are applied, so a caller who
interrupts the greeting cancels node setup and the LLM runs unconfigured.
Each node is built from the session's Context, so the same graph drives any
screening type.

Prompt architecture note: the binding behaviour (stay in role, be terse, and —
critically — CALL THE TOOLS) lives in each node's ``role_message``, because
pipecat-flows delivers that as the LLM *system* instruction. ``task_messages``
are demoted to a user-role message by the Anthropic adapter, so anything the
agent must actually obey cannot live there — it would arrive as if the caller
said it. Keep ``task_messages`` as a short nudge; keep authority in role_message.
"""

from loguru import logger
from pipecat_flows import FlowArgs, FlowManager, FlowsFunctionSchema, NodeConfig

from app.contexts.schema import Context
from app.session import SessionState
from app.verify.tool import make_verify_claim_schema

# Hard constraints shared by every node. This is spoken aloud over a phone, and
# the agent must not behave like a general-purpose chat assistant.
_RULES = (
    "You are on a live phone call and your words are read aloud by a "
    "text-to-speech engine. Rules you must always follow:\n"
    "- Reply in at most one or two short spoken sentences.\n"
    "- Never use lists, numbered points, bullets, markdown, asterisks, headings, "
    "or emojis. Write plain spoken prose.\n"
    "- Do not be sycophantic. Skip openers like 'Great question', 'Absolutely', "
    "'That's awesome', 'I'd be happy to'. Get to the point.\n"
    "- Stay strictly in your role. Do not answer the caller's general questions, "
    "give advice, or chitchat. If they go off-topic, briefly redirect to your task."
)


def _persona(ctx: Context) -> str:
    return (
        f"You are the voice screening agent for {ctx.display_name}. "
        "You are warm but genuinely probing, and you listen for specifics. "
        f"{_RULES}"
    )


# --- collect_anchors (initial node) ---
def make_collect_anchors_node(session: SessionState) -> NodeConfig:
    ctx = session.context
    anchor_lines = "\n".join(
        f"- {a.key}: {a.prompt}{' (optional)' if not a.required else ''}"
        for a in ctx.required_anchors
    )

    async def record_anchors(args: FlowArgs, flow_manager: FlowManager):
        session.set_anchors(
            name=args.get("name"),
            company=args.get("company"),
            email=args.get("email"),
            profile_url=args.get("profile_url"),
        )
        logger.info(f"anchors collected: {session.anchors.model_dump(exclude_none=True)}")
        return {"status": "recorded"}, make_questioning_node(session)

    record_anchors_schema = FlowsFunctionSchema(
        name="record_anchors",
        description=(
            "Record the caller's identity anchors. Call this as soon as you have "
            "their name, company/school, and email."
        ),
        properties={
            "name": {"type": "string", "description": "Caller's full name"},
            "company": {"type": "string", "description": "Where they work or study"},
            "email": {"type": "string", "description": "Best contact email"},
            "profile_url": {
                "type": "string",
                "description": "One link to their work (GitHub/LinkedIn/X), if given",
            },
        },
        required=["name", "company", "email"],
        handler=record_anchors,
    )

    role_message = (
        f"{_persona(ctx)}\n\n"
        "Your only task right now is to collect three things from the caller, one "
        "at a time and briefly:\n"
        f"{anchor_lines}\n"
        "Ask for whichever you don't have yet. Read the email back once to confirm "
        "it. The moment you have the name, company/school, and email, you MUST call "
        "the record_anchors tool with everything gathered — that is the only way to "
        "move forward. Until you have all three, keep asking for the missing one; do "
        "not interview them, answer their questions, or make small talk."
    )

    return NodeConfig(
        name="collect_anchors",
        role_message=role_message,
        task_messages=[
            {
                "role": "system",
                "content": (
                    "Collect the caller's name, company/school, and email, then call "
                    "record_anchors."
                ),
            }
        ],
        functions=[record_anchors_schema],
        respond_immediately=False,  # wait for the caller to answer the greeting
    )


# --- questioning ---
def make_questioning_node(session: SessionState) -> NodeConfig:
    ctx = session.context
    questions = ctx.question_bank[: ctx.max_questions]
    question_lines = "\n".join(f"- [{q.id}] {q.text}" for q in questions)

    async def wrap_up(args: FlowArgs, flow_manager: FlowManager):
        session.completed = True
        return {"status": "wrapping_up"}, make_close_node(session)

    wrap_up_schema = FlowsFunctionSchema(
        name="wrap_up",
        description="End the interview once all questions are asked or the caller is done.",
        properties={},
        required=[],
        handler=wrap_up,
    )

    role_message = (
        f"{_persona(ctx)}\n\n"
        "You are now interviewing the caller. Ask these questions in order, one at a "
        "time, with at most one short follow-up each:\n"
        f"{question_lines}\n"
        "When the caller states a concrete factual claim (a specific project, role, "
        "employer, tool, or contribution), you MUST call the verify_claim tool with "
        "that claim, then continue the interview. Track how many questions you've "
        f"asked: ask at most {ctx.max_questions}. When you have asked them all, or the "
        "caller wants to finish, you MUST call wrap_up."
    )

    return NodeConfig(
        name="questioning",
        role_message=role_message,
        task_messages=[
            {
                "role": "system",
                "content": (
                    "Ask the next interview question. Call verify_claim on concrete "
                    "claims; call wrap_up when finished."
                ),
            }
        ],
        functions=[make_verify_claim_schema(session), wrap_up_schema],
    )


# --- close ---
def make_close_node(session: SessionState) -> NodeConfig:
    ctx = session.context
    return NodeConfig(
        name="close",
        role_message=(
            f"{_persona(ctx)}\n\n"
            f"Say exactly this and nothing else, then stop: {ctx.close_script}"
        ),
        task_messages=[
            {"role": "system", "content": f"Say the closing line: {ctx.close_script}"}
        ],
        post_actions=[{"type": "end_conversation"}],
    )


def build_flow_manager(task, llm, context_aggregator, transport) -> FlowManager:
    return FlowManager(
        task=task,
        llm=llm,
        context_aggregator=context_aggregator,
        transport=transport,
    )
