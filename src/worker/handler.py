"""Lambda entrypoint for worker."""

import logging
from typing import Any, Dict

from src.worker.run import run

logger = logging.getLogger(__name__)


def handler(event: Dict[str, Any], context: Any = None) -> dict:
    """Lambda handler. Receives s3_location and internal_bucket."""
    s3_location = event.get("s3_location", "")
    internal_bucket = event.get("internal_bucket", "")

    if not s3_location:
        logger.error("Missing s3_location in event")
        return {"status": "failed", "error": "Missing s3_location"}

    try:
        status = run(s3_location, internal_bucket)
        return {"status": status}
    except Exception as e:
        logger.exception("Worker failed")
        return {"status": "failed", "error": str(e)}
