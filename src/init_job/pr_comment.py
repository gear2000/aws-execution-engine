"""Post initial PR comment with order summary."""

from typing import Dict, List, Optional

from src.common.models import Job, QUEUED
from src.common.vcs.helper import VcsHelper


def _build_comment_body(
    job: Job,
    run_id: str,
    flow_id: str,
    search_tag: str,
    repackaged_orders: List[Dict],
) -> str:
    """Build the initial PR comment body with order summary."""
    lines = []

    # Order summary table
    lines.append("**Order Summary**")
    lines.append("")

    for i, order_info in enumerate(repackaged_orders):
        name = order_info["order_name"]
        prefix = "\u2514\u2500" if i == len(repackaged_orders) - 1 else "\u251c\u2500"
        lines.append(f"{prefix} {name}: {QUEUED}")

    # Tag block on last line for search
    tag_line = VcsHelper.format_tags(search_tag, [f"#{run_id}", f"#{flow_id}"])
    lines.append("")
    lines.append(tag_line)

    return "\n".join(lines)


def init_pr_comment(
    job: Job,
    run_id: str,
    flow_id: str,
    search_tag: str,
    repackaged_orders: List[Dict],
    vcs_provider: str = "github",
) -> Optional[int]:
    """Post initial PR comment on the PR/issue.

    Returns the comment ID if posted, None if no PR/issue number.
    """
    pr_number = job.pr_number or job.issue_number
    if not pr_number:
        return None

    comment_body = _build_comment_body(
        job=job,
        run_id=run_id,
        flow_id=flow_id,
        search_tag=search_tag,
        repackaged_orders=repackaged_orders,
    )

    vcs = VcsHelper(provider=vcs_provider)

    # Fetch the git token (for now assume it's passed; in production
    # it would be fetched from SSM/Secrets Manager)
    token = getattr(job, "_git_token", "")

    comment_id = vcs.upsert_comment(
        repo=job.git_repo,
        pr_number=pr_number,
        search_str=search_tag,
        comment_body=comment_body,
        tags=[f"#{run_id}", f"#{flow_id}"],
        token=token,
    )

    return comment_id.get("comment_id")
