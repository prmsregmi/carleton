"""Offline demo: write a tiny, hand-built knowledge graph to an Obsidian vault.

No Recall, no API keys, no extraction agent yet — this hard-codes the nodes the
SQL->Postgres meeting *should* produce, so you can open the result in Obsidian
and judge whether the network shape is right before we automate it. Later, the
extraction brain + resolve + merge will produce these same nodes from a real
transcript.

    uv run python -m app.demo            # writes ./demo-vault
    uv run python -m app.demo /tmp/vault # custom path
"""

import sys
from datetime import date, datetime

from app.graph.schema import (
    ActionItem,
    Decision,
    Meeting,
    Person,
    Project,
)
from app.graph.vault import Vault
from app.meetings.schema import AttributionSource, SpeakerAttribution

NOW = datetime(2026, 5, 28, 15, 0)


def build_nodes() -> list:
    """The graph one infra-sync meeting should leave behind."""
    ahmed = Person(
        slug="Ahmed Ismail",
        title="Ahmed Ismail",
        email="ahmed@example.com",
        aliases=["Ahmed's iPhone"],  # a noisy label a future meeting will auto-resolve
        role="Backend Engineer",
        team="Platform",
        expertise=["Postgres", "Python", "data migrations"],
        responsibilities=["Owns the SQL -> Postgres migration"],
        current_projects=["Postgres Migration"],
        open_action_items=["Migrate primary DB from SQL to Postgres"],
        comms_style="terse, prefers async follow-ups",
        body="Picked up the database migration in this meeting.",
        created_at=NOW,
        updated_at=NOW,
    )
    sara = Person(
        slug="Sara Chen",
        title="Sara Chen",
        email="sara@example.com",
        role="Tech Lead",
        team="Platform",
        expertise=["system design", "schema modelling"],
        current_projects=["Postgres Migration"],
        open_action_items=["Audit schema dependencies before migration"],
        body="Approved the migration and asked for a dependency audit first.",
        created_at=NOW,
        updated_at=NOW,
    )

    project = Project(
        slug="Postgres Migration",
        title="Postgres Migration",
        name="Postgres Migration",
        aliases=["the migration", "pg move"],
        description="Move the primary database from SQL Server to PostgreSQL.",
        status="active",
        people=["Ahmed Ismail", "Sara Chen"],
        body="Kicked off in the 2026-05-28 infra sync.",
        created_at=NOW,
        updated_at=NOW,
    )

    decision = Decision(
        slug="Migrate primary database from SQL to Postgres",
        title="Migrate primary database from SQL to Postgres",
        statement="The primary database will move from SQL Server to PostgreSQL.",
        decided_by=["Ahmed Ismail", "Sara Chen"],
        project="Postgres Migration",
        source_meeting="2026-05-28 Infra Sync",
        rationale="Better JSONB support and lower licensing cost.",
        decided_on=date(2026, 5, 28),
        created_at=NOW,
        updated_at=NOW,
    )

    migrate = ActionItem(
        slug="Migrate primary DB from SQL to Postgres",
        title="Migrate primary DB from SQL to Postgres",
        task="Migrate the primary database from SQL Server to PostgreSQL.",
        assignee="Ahmed Ismail",
        project="Postgres Migration",
        due=date(2026, 6, 30),
        source_meeting="2026-05-28 Infra Sync",
        source_decision="Migrate primary database from SQL to Postgres",
        confidence=0.92,
        body="Grounded in: \"we should migrate SQL to Postgres this quarter\".",
        created_at=NOW,
        updated_at=NOW,
    )
    audit = ActionItem(
        slug="Audit schema dependencies before migration",
        title="Audit schema dependencies before migration",
        task="Audit application schema dependencies before the migration starts.",
        assignee="Sara Chen",
        project="Postgres Migration",
        source_meeting="2026-05-28 Infra Sync",
        confidence=0.81,
        body="Grounded in: \"let's audit what depends on the schema first\".",
        created_at=NOW,
        updated_at=NOW,
    )

    meeting = Meeting(
        slug="2026-05-28 Infra Sync",
        title="2026-05-28 Infra Sync",
        meeting_id="m-2026-05-28-infra",
        held_on=date(2026, 5, 28),
        attendees=["Ahmed Ismail", "Sara Chen"],
        speaker_map=[
            SpeakerAttribution(
                label="Ahmed Ismail",
                person="Ahmed Ismail",
                confidence=0.95,
                source=AttributionSource.NAME_MATCH,
            ),
            SpeakerAttribution(
                label="Speaker 2",
                person="Sara Chen",
                confidence=0.62,
                source=AttributionSource.ELIMINATION,
            ),
            # Left unresolved on purpose — this is what the human confirms in review.
            SpeakerAttribution(label="Speaker 3", person=None),
        ],
        decisions=["Migrate primary database from SQL to Postgres"],
        action_items=[
            "Migrate primary DB from SQL to Postgres",
            "Audit schema dependencies before migration",
        ],
        body="Infra sync. Agreed to migrate the primary DB to Postgres this quarter.",
        created_at=NOW,
        updated_at=NOW,
    )

    return [ahmed, sara, project, decision, migrate, audit, meeting]


def main() -> None:
    root = sys.argv[1] if len(sys.argv) > 1 else "demo-vault"
    vault = Vault(root)
    nodes = build_nodes()
    paths = vault.write_all(nodes)

    print(f"Wrote {len(paths)} notes to {vault.root}/\n")
    for p in paths:
        print(f"  {p.relative_to(vault.root)}")

    # Prove the read path round-trips (resolve.py will rely on this).
    sample = vault.read(vault.path_for(nodes[3]))  # the Decision
    print(f"\nRound-trip read of '{sample.slug}': type={sample.type}, "
          f"keys={sorted(sample.frontmatter)}")
    print(f"\nOpen {vault.root}/ as an Obsidian vault and check the graph view.")


if __name__ == "__main__":
    main()
