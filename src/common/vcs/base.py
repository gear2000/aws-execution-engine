"""Abstract base class for VCS providers."""

from abc import ABC, abstractmethod
from typing import Optional


class VcsProvider(ABC):
    """Abstract base class for Version Control System providers."""

    @abstractmethod
    def verify_webhook(self, headers: dict, body: bytes, secret: str) -> bool:
        """Verify webhook signature. Returns True if valid."""
        ...

    @abstractmethod
    def create_comment(self, repo: str, pr_number: int, body: str, token: str) -> int:
        """Create a comment on a PR. Returns comment_id."""
        ...

    @abstractmethod
    def update_comment(self, repo: str, comment_id: int, body: str, token: str) -> bool:
        """Update an existing comment. Returns True if successful."""
        ...

    @abstractmethod
    def find_comment_by_tag(
        self, repo: str, pr_number: int, tag: str, token: str
    ) -> Optional[int]:
        """Find a comment containing a tag string. Returns comment_id or None."""
        ...

    @abstractmethod
    def delete_comment(self, repo: str, comment_id: int, token: str) -> bool:
        """Delete a comment. Returns True if successful."""
        ...
