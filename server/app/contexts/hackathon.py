"""Seed context: hackathon applicant screening.

The agent qualifies a builder's credibility and the truth of their project /
experience claims, then cross-references against their public presence.
"""

from app.contexts.schema import (
    AnchorSpec,
    Context,
    QuestionItem,
    RubricItem,
    ScreeningType,
)

HACKATHON_CONTEXT = Context(
    screening_type=ScreeningType.HACKATHON_APPLICANT,
    display_name="Hackathon Applicant Screening",
    intro_script=(
        "Hi, thanks for calling in. I'm the screening agent for the hackathon. "
        "This is a quick chat to learn about you and what you've built. "
        "To start, could you tell me your name?"
    ),
    required_anchors=[
        AnchorSpec(key="name", prompt="What's your full name?", validate_as=None),
        AnchorSpec(
            key="company",
            prompt="Where do you currently work or study?",
            validate_as=None,
        ),
        AnchorSpec(
            key="email",
            prompt="What's the best email to reach you at?",
            validate_as="email",
        ),
        AnchorSpec(
            key="profile_url",
            prompt=(
                "Could you share one link that best represents your work — GitHub, LinkedIn, or X?"
            ),
            validate_as="url",
            required=False,
        ),
    ],
    question_bank=[
        QuestionItem(
            id="q_project",
            text="What's the most technically demanding thing you've built, and what was your specific role?",
            follow_up_hint="Probe for the hardest sub-problem and how they solved it.",
        ),
        QuestionItem(
            id="q_stack",
            text="What tools or frameworks did you reach for there, and why those over the alternatives?",
            follow_up_hint="Listen for a real tradeoff, not buzzwords.",
        ),
        QuestionItem(
            id="q_failure",
            text="Tell me about a time that build broke in a way you didn't expect. What was the root cause?",
            follow_up_hint="Genuine debugging stories are hard to fake.",
        ),
        QuestionItem(
            id="q_shipped",
            text="Is it live or used by anyone? Where can people see it?",
            follow_up_hint="Cross-reference against GitHub activity / a live URL.",
        ),
        QuestionItem(
            id="q_oss",
            text="Have you contributed to any open-source projects? Which ones?",
            follow_up_hint="Verifiable against GitHub.",
        ),
    ],
    rubric=[
        RubricItem(id="depth", criterion="Demonstrates genuine technical depth", weight=2.0),
        RubricItem(id="ownership", criterion="Clearly owned the work they describe", weight=1.5),
        RubricItem(
            id="consistency",
            criterion="Claims are consistent with public evidence",
            weight=2.0,
        ),
        RubricItem(id="communication", criterion="Explains clearly under questioning", weight=1.0),
    ],
    close_script=(
        "That's everything I needed. Thanks for walking me through your work — "
        "we'll be in touch with next steps. Have a great rest of your day!"
    ),
    max_questions=5,
    corroboration_threshold=0.6,
)
