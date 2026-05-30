"""Build the extractor's conditioning from the existing vault.

Turns the meeting's attendee list (emails, from the calendar) + whatever the
graph already knows about them and the projects into an `ExtractionContext`. This
is the bridge that makes extraction *personalized*: a returning teammate arrives
with their accumulated Person node, so the brain attributes and enriches against
a real profile instead of a blank slate.
"""

from app.extract.schema import ExtractionContext, PersonBrief, ProjectBrief
from app.graph.schema import NodeType
from app.graph.vault import Vault, dewikify
from app.meetings.schema import Transcript


def _strs(value) -> list[str]:
    """Frontmatter list field -> list[str], stripping any wikilink wrapping."""
    if not isinstance(value, list):
        return []
    return [dewikify(str(v)) for v in value]


def build_context(vault: Vault, transcript: Transcript) -> ExtractionContext:
    people = vault.list(NodeType.PERSON)
    by_email = {
        str(p.get("email")).lower(): p for p in people if p.get("email")
    }

    attendees: list[PersonBrief] = []
    for a in transcript.meta.attendees:
        doc = by_email.get(a.email.lower())
        if doc is not None:
            attendees.append(
                PersonBrief(
                    slug=doc.slug,
                    name=str(doc.get("title", doc.slug)),
                    email=a.email,
                    role=doc.get("role") and str(doc.get("role")),
                    expertise=_strs(doc.get("expertise")),
                    responsibilities=_strs(doc.get("responsibilities")),
                    current_projects=_strs(doc.get("current_projects")),
                )
            )
        else:
            # Unknown teammate — first time we've seen them; seed a minimal brief.
            name = a.display_name or a.email
            attendees.append(PersonBrief(slug=name, name=name, email=a.email))

    known_projects = [
        ProjectBrief(
            slug=pr.slug,
            name=str(pr.get("name", pr.slug)),
            aliases=_strs(pr.get("aliases")),
        )
        for pr in vault.list(NodeType.PROJECT)
    ]

    return ExtractionContext(
        meeting_title=transcript.meta.title,
        held_on=transcript.meta.started_at.date().isoformat() if transcript.meta.started_at else None,
        attendees=attendees,
        known_projects=known_projects,
    )
