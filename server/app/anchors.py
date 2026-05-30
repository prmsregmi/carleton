"""Caller identity anchors collected during the call.

These are the self-provided facts the verification brain corroborates against
public sources. `email` is kept as a plain string (no email-validator dep) to
stay lightweight; light validation happens in the collect_anchors node.
"""

from pydantic import BaseModel


class CallerAnchors(BaseModel):
    name: str | None = None
    company: str | None = None
    email: str | None = None
    profile_url: str | None = None  # one self-provided link (linkedin/x/github/site)
    location: str | None = None  # optional, captured only if volunteered

    def has_required(self) -> bool:
        """Minimum needed before questioning + a memory lookup are meaningful."""
        return bool(self.name and self.company and self.email)
