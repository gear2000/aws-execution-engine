"""Unit tests for src/common/vcs/github.py using responses library."""

import hashlib
import hmac
import json

import pytest
import responses

from src.common.vcs.base import VcsProvider
from src.common.vcs.github import GitHubProvider, GITHUB_API_BASE


@pytest.fixture
def github():
    return GitHubProvider()


# ---------------------------------------------------------------------------
# Tag block helpers (tested on the base class static methods)
# ---------------------------------------------------------------------------


class TestFormatTags:
    def test_search_str_only(self):
        result = VcsProvider.format_tags("my-run")
        assert result == "###my-run###"

    def test_with_tags(self):
        result = VcsProvider.format_tags("my-run", ["deploy", "vpc"])
        assert result == "###my-run### #deploy #vpc"

    def test_empty_tags_list(self):
        result = VcsProvider.format_tags("my-run", [])
        assert result == "###my-run###"


class TestHasTagBlockAtLastLine:
    def test_match_last_line(self):
        body = "Some status update\nMore details\n###my-run### #deploy"
        assert VcsProvider.has_tag_block_at_last_line(body, "my-run") is True

    def test_match_with_required_tags(self):
        body = "Status\n###my-run### #deploy #vpc"
        assert VcsProvider.has_tag_block_at_last_line(body, "my-run", ["deploy", "vpc"]) is True

    def test_missing_required_tag(self):
        body = "Status\n###my-run### #deploy"
        assert VcsProvider.has_tag_block_at_last_line(body, "my-run", ["deploy", "vpc"]) is False

    def test_no_tag_block(self):
        body = "Just a regular comment"
        assert VcsProvider.has_tag_block_at_last_line(body, "my-run") is False

    def test_tag_in_middle_not_last_line(self):
        """Tag block in the middle of the comment should NOT match."""
        body = "###my-run### #deploy\nSome other text after"
        assert VcsProvider.has_tag_block_at_last_line(body, "my-run") is False

    def test_trailing_whitespace(self):
        body = "Status\n###my-run### #deploy  \n  "
        assert VcsProvider.has_tag_block_at_last_line(body, "my-run") is True

    def test_search_str_only_no_tags(self):
        body = "Status\n###my-run###"
        assert VcsProvider.has_tag_block_at_last_line(body, "my-run") is True

    def test_wrong_search_str(self):
        body = "Status\n###other-run### #deploy"
        assert VcsProvider.has_tag_block_at_last_line(body, "my-run") is False

    def test_empty_body(self):
        assert VcsProvider.has_tag_block_at_last_line("", "my-run") is False


# ---------------------------------------------------------------------------
# Webhook verification
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# CRUD
# ---------------------------------------------------------------------------


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


# ---------------------------------------------------------------------------
# find_comment_by_tag — whole-body substring search
# ---------------------------------------------------------------------------


class TestFindCommentByTag:
    @responses.activate
    def test_finds_tag_anywhere_in_body(self, github):
        """find_comment_by_tag searches the entire body, not just last line."""
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[
                {"id": 1, "body": "unrelated comment"},
                {"id": 2, "body": "contains #search-tag-123 in the middle\nmore text"},
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
            json=[{"id": i, "body": "no match"} for i in range(100)],
            status=200,
        )
        # Second page: match found
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[{"id": 200, "body": "has #mytag inside"}],
            status=200,
        )

        result = github.find_comment_by_tag("org/repo", 42, "#mytag", "token")
        assert result == 200

    @responses.activate
    def test_not_found(self, github):
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[{"id": 1, "body": "no match"}],
            status=200,
        )

        result = github.find_comment_by_tag("org/repo", 42, "#notfound", "token")
        assert result is None


# ---------------------------------------------------------------------------
# search_comments — strict last-line tag block search
# ---------------------------------------------------------------------------


class TestSearchComments:
    @responses.activate
    def test_finds_matching_tag_block(self, github):
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[
                {"id": 1, "body": "unrelated comment"},
                {"id": 2, "body": "Deploy status\n\n###my-run### #deploy"},
            ],
            status=200,
        )

        ids = github.search_comments("org/repo", 42, "my-run", "token")
        assert ids == [2]

    @responses.activate
    def test_ignores_tag_not_on_last_line(self, github):
        """Tag block in the middle of comment body must NOT match."""
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[
                {"id": 1, "body": "###my-run### #deploy\nExtra text after"},
            ],
            status=200,
        )

        ids = github.search_comments("org/repo", 42, "my-run", "token")
        assert ids == []

    @responses.activate
    def test_filters_by_required_tags(self, github):
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[
                {"id": 1, "body": "VPC plan\n\n###my-run### #deploy #vpc"},
                {"id": 2, "body": "RDS plan\n\n###my-run### #deploy #rds"},
            ],
            status=200,
        )

        ids = github.search_comments("org/repo", 42, "my-run", "token", tags=["vpc"])
        assert ids == [1]

    @responses.activate
    def test_returns_empty_when_no_match(self, github):
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[{"id": 1, "body": "no tag block here"}],
            status=200,
        )

        ids = github.search_comments("org/repo", 42, "my-run", "token")
        assert ids == []

    @responses.activate
    def test_pagination(self, github):
        # Page 1: no match (full page of 100)
        page1 = [{"id": i, "body": "no match"} for i in range(100)]
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=page1,
            status=200,
        )
        # Page 2: has match (partial page)
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[{"id": 200, "body": "found it\n###my-run###"}],
            status=200,
        )

        ids = github.search_comments("org/repo", 42, "my-run", "token")
        assert ids == [200]
        assert len(responses.calls) == 2


# ---------------------------------------------------------------------------
# upsert_comment — uses last-line search
# ---------------------------------------------------------------------------


class TestUpsertComment:
    @responses.activate
    def test_creates_when_not_found(self, github):
        # search returns no matches
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[],
            status=200,
        )
        # create comment
        responses.add(
            responses.POST,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json={"id": 555, "body": "new"},
            status=201,
        )

        result = github.upsert_comment(
            "org/repo", 42, "my-run", "Deploy report", ["deploy"], "token"
        )

        assert result["id"] == 555
        assert result["action"] == "created"
        assert result["status"] is True

        # Verify the body includes the tag block
        created_body = json.loads(responses.calls[1].request.body)["body"]
        assert created_body.endswith("###my-run### #deploy")

    @responses.activate
    def test_updates_when_found(self, github):
        # search returns a match
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[
                {"id": 100, "body": "Old content\n\n###my-run### #deploy"},
            ],
            status=200,
        )
        # update comment
        responses.add(
            responses.PATCH,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/comments/100",
            json={"id": 100, "body": "updated"},
            status=200,
        )

        result = github.upsert_comment(
            "org/repo", 42, "my-run", "New deploy report", ["deploy"], "token"
        )

        assert result["id"] == 100
        assert result["action"] == "updated"
        assert result["status"] is True

        # Verify the body was updated with tag block
        updated_body = json.loads(responses.calls[1].request.body)["body"]
        assert "New deploy report" in updated_body
        assert updated_body.endswith("###my-run### #deploy")

    @responses.activate
    def test_upsert_with_multiple_tags(self, github):
        # search returns no matches
        responses.add(
            responses.GET,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json=[],
            status=200,
        )
        # create
        responses.add(
            responses.POST,
            f"{GITHUB_API_BASE}/repos/org/repo/issues/42/comments",
            json={"id": 777, "body": "created"},
            status=201,
        )

        result = github.upsert_comment(
            "org/repo", 42, "run-abc", "Plan output", ["plan", "vpc"], "token"
        )

        assert result["id"] == 777
        created_body = json.loads(responses.calls[1].request.body)["body"]
        assert created_body.endswith("###run-abc### #plan #vpc")
