"""The rolling-extraction artifact.

Short-term working memory for one meeting, refreshed incrementally on the
extraction interval. `context` is prose for the brain to read; `open_tasks` are
addressable units of work (executed live via the wake word or in a batch at the
end, both through the same `handle_request`); `preference_candidates` are
team-practice notes promoted to long-term Obsidian memory at meeting end.
"""

from typing import Literal

from pydantic import BaseModel, Field

TaskStatus = Literal["pending", "done"]


class Task(BaseModel):
    text: str
    status: TaskStatus = "pending"


class RollingExtraction(BaseModel):
    context: str = ""
    open_tasks: list[Task] = Field(default_factory=list)
    preference_candidates: list[str] = Field(default_factory=list)
