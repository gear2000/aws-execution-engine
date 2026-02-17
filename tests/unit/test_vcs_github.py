"""Unit tests for src/common/vcs/github.py using responses library."""

import hashlib
import hmac
import json

import pytest
import responses
from responses import matchers

from src.common.vcs.github import GitHubProvider, GITHUB_API_BASE


@pytest.fixture
def github():
    return GitHubProvider()


class TestVerifyWebhook:
    def test_valid_signature(self, github):
        secret = "mysecret"
        body = b'{"action":"opened"}'
        sig = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
        headers = {"X-Hub-Signature-256": f"sha256={sig}"}

        assert github.verify_webhook(headers, body, secret) is True

    def test_invalid_signature(self, github):
        headers = {"X-Hub-Signature-256": "sha256=invalid"}
        assert github.verify_webhook(headers, b"body", "secret") is False

    def test_missing_signature(self, github):
        assert github.verify_webhook({}, b"body", "secret") is False

    def test_wrong_prefix(self, github):
        headers = {"X-Hub-Signature-256": "md5=abc"}
        assert github.verify_webhook(headers, b"body", "secret") is False


class TestCreateComment:
    @responses.activate
    def test_create_comment(self, github):
        responses.add(
            responses.POST,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json={"id": 12345, "body": "test comment"},
            status=201,
        )

        comment_id = github.create_comment("org/repo", 42, "test comment", "token123")
        assert comment_id == 12345

        # Verify request
        assert len(responses.calls) == 1
        req = responses.calls[0].request
        assert "Bearer token123" in req.headers["Authorization"]
        assert json.loads(req.body)["body"] == "test comment"


class TestUpdateComment:
    @responses.activate
    def test_update_comment_success(self, github):
        responses.add(
            responses.PATCH,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/comments/12345",
            json={"id": 12345, "body": "updated"},
            status=200,
        )

        result = github.update_comment("org/repo", 12345, "updated", "token123")
        assert result is True

    @responses.activate
    def test_update_comment_failure(self, github):
        responses.add(
            responses.PATCH,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/comments/12345",
            json={"message": "Not Found"},
            status=404,
        )

        result = github.update_comment("org/repo", 12345, "updated", "token123")
        assert result is False


class TestFindCommentByTag:
    @responses.activate
    def test_find_on_first_page(self, github):
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[
                {"id": 1, "body": "unrelated comment"},
                {"id": 2, "body": "contains #search-tag-123 here"},
            ],
            status=200,
        )

        result = github.find_comment_by_tag("org/repo", 42, "#search-tag-123", "token")
        assert result == 2

    @responses.activate
    def test_find_with_pagination(self, github):
        # First page: no match
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[{"id": 1, "body": "page 1 comment"}],
            status=200,
        )
        # Second page: match found
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[{"id": 2, "body": "has #mytag inside"}],
            status=200,
        )
        # Third page: empty (end)
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[],
            status=200,
        )

        result = github.find_comment_by_tag("org/repo", 42, "#mytag", "token")
        assert result == 2

    @responses.activate
    def test_not_found(self, github):
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[{"id": 1, "body": "no match"}],
            status=200,
        )
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[],
            status=200,
        )

        result = github.find_comment_by_tag("org/repo", 42, "#notfound", "token")
        assert result is None


class TestDeleteComment:
    @responses.activate
    def test_delete_success(self, github):
        responses.add(
            responses.DELETE,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/comments/12345",
            status=204,
        )

        result = github.delete_comment("org/repo", 12345, "token")
        assert result is True

    @responses.activate
    def test_delete_failure(self, github):
        responses.add(
            responses.DELETE,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/comments/99999",
            status=404,
        )

        result = github.delete_comment("org/repo", 99999, "token")
        assert result is False
