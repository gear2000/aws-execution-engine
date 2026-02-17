"""Abstract base class for VCS providers."""

import re
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional


class VcsProvider(ABC):
    """Abstract base class for Version Control System providers.

    Tag block format (always the last line of a managed comment):
        ###search_str### #tag1 #tag2

    Two search strategies:
    - find_comment_by_tag: general whole-body substring search
    - search_comments: strict last-line tag block search (used by upsert_comment)
    """

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
        self, repo: str, pr_number: int, tag: str, token: str,
    ) -> Optional[int]:
        """Find a comment containing a tag substring anywhere in the body.

        General-purpose whole-body search.
        Returns the first matching comment_id, or None.
        """
        ...

    @abstractmethod
    def search_comments(
        self, repo: str, pr_number: int, search_str: str, token: str,
        tags: Optional[List[str]] = None,
    ) -> List[int]:
        """Find all comments whose last line contains a matching tag block.

        Strict last-line search using ###search_str### #tag format.
        Returns list of matching comment IDs.
        """
        ...

    @abstractmethod
    def delete_comment(self, repo: str, comment_id: int, token: str) -> bool:
        """Delete a comment. Returns True if successful."""
        ...

    @abstractmethod
    def upsert_comment(
        self, repo: str, pr_number: int, search_str: str,
        comment_body: str, tags: List[str], token: str,
    ) -> Dict[str, Any]:
        """Create comment if not found, update if found.

        Uses last-line tag block search to find existing comments.
        The tag block is automatically appended to comment_body.
        Returns dict with keys: id, status, action ("created" or "updated").
        """
        ...

    # --- helpers (concrete, shared by all providers) ---

    @staticmethod
    def format_tags(search_str: str, tags: Optional[List[str]] = None) -> str:
        """Format the tag block that goes on the last line of a comment.

        Example: format_tags("my-run", ["deploy", "vpc"]) -> "###my-run### #deploy #vpc"
        """
        tag_line = f"###{search_str}###"
        if tags:
            tag_suffix = " ".join(f"#{t}" for t in tags if t)
            tag_line = f"{tag_line} {tag_suffix}"
        return tag_line

    @staticmethod
    def has_tag_block_at_last_line(
        comment_body: str, search_str: str, tags: Optional[List[str]] = None,
    ) -> bool:
        """Check if the last non-empty line is a matching tag block.

        Matches: ###search_str### optionally followed by #tag1 #tag2 ...
        If tags are provided, all must be present.
        """
        lines = comment_body.strip().split("\n")
        last_line = None
        for line in reversed(lines):
            stripped = line.strip()
            if stripped:
                last_line = stripped
                break

        if not last_line:
            return False

        pattern = rf"^###{re.escape(search_str)}###\s*(.*)$"
        match = re.match(pattern, last_line)
        if not match:
            return False

        if tags:
            tags_str = match.group(1).strip()
            existing = {
                t.lstrip("#") for t in tags_str.split() if t.startswith("#")
            }
            return all(t in existing for t in tags if t)

        return True
