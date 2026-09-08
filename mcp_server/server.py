"""MCP facade for TelePost review workflows.

Runs as a separate process and only speaks to TelePost's internal review HTTP
API. It never opens SQLite, constructs Telegram calls, or fakes a PTB Update.
"""

from __future__ import annotations

import asyncio
import base64
import json
import os
from typing import Any, Dict, Literal, Optional

try:
    from mcp.server import MCPServer
    from mcp.server.mcpserver import Image
    from mcp.types import ToolAnnotations
except ImportError:  # Optional sidecar dependency; TelePost core must still import.
    MCPServer = None
    Image = None
    ToolAnnotations = None

from mcp_server.review_client import ReviewApiClient, ReviewApiError


READ_ONLY = os.getenv("TELEPOST_MCP_REVIEW_MODE", "readwrite").strip().lower() == "readonly"
WRITE_TOOLS = ("approve_review", "reject_review", "set_review_spoiler")


def _client() -> ReviewApiClient:
    return ReviewApiClient()


def _error(exc: ReviewApiError) -> Dict[str, Any]:
    return exc.to_dict()


def _readonly_error() -> Dict[str, Any]:
    return _error(ReviewApiError(
        "permission_denied", "MCP server is configured read-only", 403
    ))


async def list_pending_reviews(limit: int = 20, cursor: Optional[str] = None) -> Dict[str, Any]:
    """List lightweight pending TelePost review summaries, newest first. Read-only."""
    try:
        return await asyncio.to_thread(_client().list_pending, limit, cursor)
    except ReviewApiError as exc:
        return _error(exc)


async def get_review(review_id: int) -> Dict[str, Any]:
    """Get full text metadata and media references for one review. Read-only."""
    try:
        return await asyncio.to_thread(_client().get_review, review_id)
    except ReviewApiError as exc:
        return _error(exc)


async def get_review_media(
    review_id: int,
    index: int,
    variant: Literal["thumbnail", "preview", "original"] = "preview",
):
    """Fetch a bounded image preview for review media. Read-only."""
    try:
        payload = await asyncio.to_thread(
            _client().get_media, review_id, index, variant
        )
    except ReviewApiError as exc:
        return _error(exc)
    if Image is None:
        return {
            "ok": False,
            "error": {"code": "mcp_sdk_missing", "message": "Install requirements-mcp.txt"},
        }
    image = Image(
        data=base64.b64decode(payload["data_base64"]),
        format="jpeg" if payload["mime_type"] == "image/jpeg" else "png",
    )
    return [image, json.dumps(payload["metadata"], ensure_ascii=False)]


async def get_review_policy() -> str:
    """Read the administrator-configured channel review policy."""
    try:
        return await asyncio.to_thread(_client().get_policy)
    except ReviewApiError as exc:
        return json.dumps(_error(exc), ensure_ascii=False)


async def approve_review(review_id: int, spoiler: Optional[bool] = None) -> Dict[str, Any]:
    """DESTRUCTIVE state-changing action: approve and publish. Requires explicit human confirmation."""
    if READ_ONLY:
        return _readonly_error()
    try:
        return await asyncio.to_thread(_client().approve, review_id, spoiler)
    except ReviewApiError as exc:
        return _error(exc)


async def reject_review(review_id: int, reason: Optional[str] = None) -> Dict[str, Any]:
    """DESTRUCTIVE state-changing action: reject a review. Requires explicit human confirmation."""
    if READ_ONLY:
        return _readonly_error()
    try:
        return await asyncio.to_thread(_client().reject, review_id, reason)
    except ReviewApiError as exc:
        return _error(exc)


async def set_review_spoiler(review_id: int, spoiler: bool) -> Dict[str, Any]:
    """State-changing action: set spoiler before approval. Requires explicit human request."""
    if READ_ONLY:
        return _readonly_error()
    try:
        return await asyncio.to_thread(_client().set_spoiler, review_id, spoiler)
    except ReviewApiError as exc:
        return _error(exc)


def review_submission_prompt(review_id: str) -> str:
    return f"""请审核 TelePost 投稿 #{review_id}，严格按以下流程：
1. 调用 list_pending_reviews 确认该稿件仍待审核，然后调用 get_review。
2. 阅读 telepost://review-policy；若策略缺失或含糊，不确定项给 needs_human_review。
3. 对所有必要媒体调用 get_review_media，默认使用 preview；不要默认 original。
4. 输出建议，不调用 approve_review、reject_review、set_review_spoiler，除非用户之后明确要求并确认具体动作。
5. 使用 JSON 给出 decision(approve/reject/needs_human_review)、confidence、reasons、suggested_spoiler、suggested_tags、warnings，并明确写“未执行审核动作”。
"""


def create_server() -> Any:
    if MCPServer is None:
        raise RuntimeError(
            "MCP SDK is not installed. Install the sidecar extras with: "
            "pip install -r requirements-mcp.txt"
        )

    mcp = MCPServer("telepost-review", instructions=(
        "Read TelePost pending submissions and media, consult telepost://review-policy, "
        "and provide recommendations. Never approve, reject, retry, or change spoiler "
        "unless the human explicitly confirmed that exact action."
    ))

    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(list_pending_reviews)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(get_review)
    mcp.tool(annotations=ToolAnnotations(read_only_hint=True, open_world_hint=False))(get_review_media)
    if not READ_ONLY:
        mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True))(approve_review)
        mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=True, idempotent_hint=True))(reject_review)
        mcp.tool(annotations=ToolAnnotations(read_only_hint=False, destructive_hint=False, idempotent_hint=True))(set_review_spoiler)
    mcp.resource("telepost://review-policy", mime_type="text/markdown")(get_review_policy)

    @mcp.prompt(title="Review a TelePost submission")
    def review_submission(review_id: str) -> str:
        """Guide a read-only review recommendation; never performs write actions automatically."""
        return review_submission_prompt(review_id)

    return mcp


def main() -> None:
    transport = os.getenv("TELEPOST_MCP_TRANSPORT", "stdio").strip().lower()
    mcp = create_server()
    if transport == "stdio":
        mcp.run()
    elif transport == "streamable-http":
        mcp.run(
            transport="streamable-http",
            host=os.getenv("TELEPOST_MCP_HTTP_HOST", "127.0.0.1"),
            port=int(os.getenv("TELEPOST_MCP_HTTP_PORT", "8081")),
            streamable_http_path=os.getenv("TELEPOST_MCP_HTTP_PATH", "/mcp"),
            json_response=True,
            stateless_http=True,
        )
    else:
        raise RuntimeError("TELEPOST_MCP_TRANSPORT must be stdio or streamable-http")


if __name__ == "__main__":
    main()
