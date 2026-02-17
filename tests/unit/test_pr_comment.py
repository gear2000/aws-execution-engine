"""Unit tests for src/init_job/pr_comment.py."""

from unittest.mock import patch, MagicMock

import pytest

from src.common.models import Job, Order
from src.init_job.pr_comment import init_pr_comment, _build_comment_body


def _make_job(orders=None, **kwargs):
    defaults = {
        "git_repo": "org/repo",
        "git_token_location": "aws:::ssm:/token",
        "username": "testuser",
        "pr_number": 42,
    }
    defaults.update(kwargs)
    return Job(orders=orders or [], **defaults)


class TestBuildCommentBody:
    def test_includes_order_summary(self):
        job = _make_job(orders=[
            Order(cmds=["echo"], timeout=300),
            Order(cmds=["echo"], timeout=300),
        ])
        repackaged = [
            {"order_num": "0001", "order_name": "deploy-vpc"},
            {"order_num": "0002", "order_name": "deploy-rds"},
        ]

        body = _build_comment_body(
            job=job,
            run_id="run-1",
            flow_id="user:abc-exec",
            search_tag="tag123",
            repackaged_orders=repackaged,
        )

        assert "deploy-vpc" in body
        assert "deploy-rds" in body
        assert "queued" in body

    def test_includes_search_tag(self):
        job = _make_job(orders=[Order(cmds=["echo"], timeout=300)])
        repackaged = [{"order_num": "0001", "order_name": "test"}]

        body = _build_comment_body(
            job=job,
            run_id="run-1",
            flow_id="user:abc-exec",
            search_tag="mysearchtag",
            repackaged_orders=repackaged,
        )

        assert "###mysearchtag###" in body

    def test_includes_run_and_flow_id_as_tags(self):
        job = _make_job(orders=[Order(cmds=["echo"], timeout=300)])
        repackaged = [{"order_num": "0001", "order_name": "test"}]

        body = _build_comment_body(
            job=job,
            run_id="run-1",
            flow_id="user:abc-exec",
            search_tag="tag",
            repackaged_orders=repackaged,
        )

        assert "#run-1" in body
        assert "#user:abc-exec" in body


class TestInitPrComment:
    @patch("src.init_job.pr_comment.VcsHelper")
    def test_posts_comment(self, MockVcsHelper):
        from src.common.vcs.helper import VcsHelper as RealVcsHelper

        mock_vcs = MagicMock()
        mock_vcs.upsert_comment.return_value = {"action": "created", "comment_id": 999}
        MockVcsHelper.return_value = mock_vcs
        # Preserve the real static method so _build_comment_body works
        MockVcsHelper.format_tags = RealVcsHelper.format_tags

        job = _make_job(orders=[Order(cmds=["echo"], timeout=300)])
        repackaged = [{"order_num": "0001", "order_name": "test"}]

        result = init_pr_comment(
            job=job,
            run_id="run-1",
            flow_id="user:abc-exec",
            search_tag="tag",
            repackaged_orders=repackaged,
        )

        assert result == 999
        mock_vcs.upsert_comment.assert_called_once()

    def test_returns_none_when_no_pr(self):
        job = _make_job(pr_number=None, issue_number=None, orders=[
            Order(cmds=["echo"], timeout=300),
        ])
        repackaged = [{"order_num": "0001", "order_name": "test"}]

        result = init_pr_comment(
            job=job,
            run_id="run-1",
            flow_id="flow",
            search_tag="tag",
            repackaged_orders=repackaged,
        )
        assert result is None
