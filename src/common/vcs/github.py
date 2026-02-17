"""GitHub VCS provider implementation."""

import hashlib
import hmac
from typing import Dict, Any, List, Optional

import requests

from .base import VcsProvider

GITHUB_API_BASE = "https://api.github.com"


class GitHubProvider(VcsProvider):
    """GitHub implementation of the VCS provider."""

    def verify_webhook(self, headers: dict, body: bytes, secret: str) -> bool:
        """Verify GitHub webhook HMAC-SHA256 signature."""
        signature = headers.get("X-Hub-Signature-256", "")
        if not signature.startswith("sha256="):
            return False
        expected = hmac.new(
            secret.encode(), body, hashlib.sha256
        ).hexdigest()
        return hmac.compare_digest(signature[7:], expected)

    def _auth_headers(self, token: str) -> dict:
        return {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github+json",
        }

    def _paginate_comments(self, repo: str, pr_number: int, token: str):
        """Yield all comments for a PR, handling pagination."""
        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{pr_number}/comments"
        page = 1
        while True:
            response = requests.get(
                url,
                params={"page": page, "per_page": 100},
                headers=self._auth_headers(token),
            )
            response.raise_for_status()
            comments = response.json()
            if not comments:
                break
            yield from comments
            if len(comments) < 100:
                break
            page += 1

    def create_comment(self, repo: str, pr_number: int, body: str, token: str) -> int:
        """POST to GitHub REST API to create a PR comment."""
        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/{pr_number}/comments"
        response = requests.post(
            url,
            json={"body": body},
            headers=self._auth_headers(token),
        )
        response.raise_for_status()
        return response.json()["id"]

    def update_comment(self, repo: str, comment_id: int, body: str, token: str) -> bool:
        """PATCH to update an existing comment."""
        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/comments/{comment_id}"
        response = requests.patch(
            url,
            json={"body": body},
            headers=self._auth_headers(token),
        )
        return response.status_code == 200

    def find_comment_by_tag(
        self, repo: str, pr_number: int, tag: str, token: str,
    ) -> Optional[int]:
        """Find a comment containing a tag substring anywhere in the body.

        General-purpose whole-body search. Returns first match or None.
        """
        for comment in self._paginate_comments(repo, pr_number, token):
            if tag in comment.get("body", ""):
                return comment["id"]
        return None

    def search_comments(
        self, repo: str, pr_number: int, search_str: str, token: str,
        tags: Optional[List[str]] = None,
    ) -> List[int]:
        """Search PR comments by tag block at the last line only.

        Returns list of all matching comment IDs.
        Handles GitHub API pagination.
        """
        matching_ids = []
        for comment in self._paginate_comments(repo, pr_number, token):
            body = comment.get("body", "")
            if self.has_tag_block_at_last_line(body, search_str, tags):
                matching_ids.append(comment["id"])
        return matching_ids

    def delete_comment(self, repo: str, comment_id: int, token: str) -> bool:
        """DELETE a comment."""
        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/comments/{comment_id}"
        response = requests.delete(url, headers=self._auth_headers(token))
        return response.status_code == 204

    def upsert_comment(
        self, repo: str, pr_number: int, search_str: str,
        comment_body: str, tags: List[str], token: str,
    ) -> Dict[str, Any]:
        """Create comment if not found, update if found.

        Uses last-line tag block search to find existing comments.
        Appends the tag block to comment_body automatically.
        """
        full_body = f"{comment_body}\n\n{self.format_tags(search_str, tags)}"

        existing_ids = self.search_comments(repo, pr_number, search_str, token, tags)

        if existing_ids:
            comment_id = existing_ids[0]
            success = self.update_comment(repo, comment_id, full_body, token)
            return {"id": comment_id, "status": success, "action": "updated"}
        else:
            comment_id = self.create_comment(repo, pr_number, full_body, token)
            return {"id": comment_id, "status": True, "action": "created"}
