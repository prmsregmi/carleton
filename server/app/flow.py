"""Conversation flow: collect_anchors -> questioning -> close.

The plan's separate "intro" stage is folded into the initial collect_anchors
node — its greeting is the node's `tts_say` pre-action — to avoid a fragile
no-input auto-transition. Each node is built from the session's Context, so the
same graph drives any screening type.
"""

from loguru import logger
from pipecat_flows import FlowArgs, FlowManager, FlowsFunctionSchema, NodeConfig

from app.contexts.schema import Context
from app.session import SessionState
from app.verify.tool import make_verify_claim_schema


def _persona(ctx: Context) -> str:
    return (
        f"You are the voice screening agent for {ctx.display_name}. "
        "You speak naturally and concisely — one or two short sentences per turn, "
        "never lists, markdown, or emojis, because your words are spoken aloud. "
        "You are warm but genuinely probing; you listen for specifics."
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
            "Record the caller's identity anchors once you have at least their "
            "name, company/school, and email."
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

    return NodeConfig(
        name="collect_anchors",
        role_message=_persona(ctx),
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Collect the following from the caller, asking for any you don't "
                    "yet have, one at a time and briefly:\n"
                    f"{anchor_lines}\n"
                    "Read the email back to confirm it. Once you have their name, "
                    "company/school, and email, call record_anchors with everything "
                    "you've gathered."
                ),
            }
        ],
        pre_actions=[{"type": "tts_say", "text": ctx.intro_script}],
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

    return NodeConfig(
        name="questioning",
        role_message=_persona(ctx),
        task_messages=[
            {
                "role": "developer",
                "content": (
                    "Interview the caller. Ask these questions in order, one at a "
                    "time, with at most one brief follow-up each:\n"
                    f"{question_lines}\n"
                    "When the caller answers with a concrete factual claim (a project, "
                    "role, tool, or contribution), call verify_claim with that claim. "
                    f"Ask at most {ctx.max_questions} questions. When you have asked "
                    "them all, or the caller wants to finish, call wrap_up."
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
        role_message=_persona(ctx),
        task_messages=[
            {
                "role": "developer",
                "content": f"Say exactly this and nothing else: {ctx.close_script}",
            }
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
