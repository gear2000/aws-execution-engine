"""GitHub VCS provider implementation."""

import hashlib
import hmac
from typing import Optional

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
        self, repo: str, pr_number: int, tag: str, token: str
    ) -> Optional[int]:
        """Search through PR comments for one containing the tag string.

        Handles GitHub API pagination.
        """
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
            for comment in comments:
                if tag in comment.get("body", ""):
                    return comment["id"]
            page += 1
        return None

    def delete_comment(self, repo: str, comment_id: int, token: str) -> bool:
        """DELETE a comment."""
        url = f"{GITHUB_API_BASE}/repos/{repo}/issues/comments/{comment_id}"
        response = requests.delete(url, headers=self._auth_headers(token))
        return response.status_code == 204
