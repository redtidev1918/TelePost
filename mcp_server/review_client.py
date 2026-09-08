"""Small HTTP client for TelePost's internal review API.

Kept dependency-free so the sidecar can run in a separate Python 3.10+ venv
without importing TelePost's Telegram application.
"""

from __future__ import annotations

import base64
import json
import os
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Dict, Optional


class ReviewApiError(Exception):
    def __init__(self, code: str, message: str, status: int = 0):
        super().__init__(message)
        self.code = code
        self.message = message
        self.status = status

    def to_dict(self) -> Dict[str, Any]:
        return {"ok": False, "error": {"code": self.code, "message": self.message}}


class ReviewApiClient:
    def __init__(
        self,
        base_url: Optional[str] = None,
        token: Optional[str] = None,
        *,
        timeout: float = 60.0,
    ):
        self.base_url = (base_url or os.getenv("TELEPOST_REVIEW_API_URL", "http://127.0.0.1:8080/api/v1")).rstrip("/")
        self.token = token if token is not None else os.getenv("TELEPOST_MCP_REVIEW_TOKEN", "")
        self.timeout = timeout
        if not self.token:
            raise ReviewApiError(
                "configuration_error",
                "TELEPOST_MCP_REVIEW_TOKEN is required",
            )

    def _request(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None):
        url = self.base_url + path
        data = None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "X-TelePost-Source": "mcp",
            "Accept": "application/json",
        }
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
            headers["Content-Type"] = "application/json"
        req = urllib.request.Request(url, data=data, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as response:
                return response.status, response.headers, response.read()
        except urllib.error.HTTPError as exc:
            body = exc.read()
            try:
                decoded = json.loads(body.decode("utf-8"))
                error = decoded.get("error") or {}
                raise ReviewApiError(
                    error.get("code", "http_error"),
                    error.get("message", exc.reason),
                    exc.code,
                ) from exc
            except (UnicodeDecodeError, json.JSONDecodeError):
                raise ReviewApiError("http_error", exc.reason, exc.code) from exc
        except urllib.error.URLError as exc:
            raise ReviewApiError("review_api_unavailable", str(exc.reason)) from exc

    def _json(self, method: str, path: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        status, _headers, body = self._request(method, path, payload)
        if status == 403:
            raise ReviewApiError("permission_denied", "Review API is read-only or token lacks permission", status)
        try:
            decoded = json.loads(body.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as exc:
            raise ReviewApiError("invalid_response", "TelePost returned invalid JSON", status) from exc
        if not decoded.get("ok", False):
            error = decoded.get("error") or {}
            raise ReviewApiError(error.get("code", "review_error"), error.get("message", ""), status)
        return decoded.get("data") or {}

    @staticmethod
    def _path(value: int) -> str:
        try:
            review_id = int(value)
        except (TypeError, ValueError) as exc:
            raise ReviewApiError("invalid_review_id", "review_id must be an integer") from exc
        if review_id <= 0:
            raise ReviewApiError("invalid_review_id", "review_id must be positive")
        return f"/reviews/{review_id}"

    def list_pending(self, limit: int = 20, cursor: Optional[str] = None) -> Dict[str, Any]:
        query = {"limit": str(max(1, min(int(limit), 100)))}
        if cursor:
            query["cursor"] = cursor
        return self._json("GET", "/reviews?" + urllib.parse.urlencode(query))

    def get_review(self, review_id: int) -> Dict[str, Any]:
        return self._json("GET", self._path(review_id))

    def get_policy(self) -> str:
        return str(self.get_review_policy().get("policy") or "")

    def get_review_policy(self) -> Dict[str, Any]:
        return self._json("GET", "/reviews/policy")

    def approve(self, review_id: int, spoiler: Optional[bool] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if spoiler is not None:
            payload["spoiler"] = bool(spoiler)
        return self._json("POST", self._path(review_id) + "/approve", payload)

    def reject(self, review_id: int, reason: Optional[str] = None) -> Dict[str, Any]:
        payload: Dict[str, Any] = {}
        if reason:
            payload["reason"] = reason
        return self._json("POST", self._path(review_id) + "/reject", payload)

    def set_spoiler(self, review_id: int, spoiler: bool) -> Dict[str, Any]:
        return self._json("PATCH", self._path(review_id) + "/spoiler", {"spoiler": bool(spoiler)})

    def get_media(self, review_id: int, index: int, variant: str = "preview") -> Dict[str, Any]:
        if variant not in {"thumbnail", "preview", "original"}:
            raise ReviewApiError("invalid_variant", "variant must be thumbnail, preview, or original")
        try:
            index_value = int(index)
        except (TypeError, ValueError) as exc:
            raise ReviewApiError("invalid_media_index", "index must be an integer") from exc
        if index_value < 0:
            raise ReviewApiError("invalid_media_index", "index must be non-negative")
        path = f"{self._path(review_id)}/media/{index_value}?variant={variant}"
        status, headers, body = self._request("GET", path)
        if not body:
            raise ReviewApiError("preview_unavailable", "Media response was empty", status)
        metadata_header = headers.get("X-Review-Media", "")
        metadata: Dict[str, Any] = {}
        if metadata_header:
            metadata = json.loads(metadata_header)
        return {
            "metadata": metadata,
            "mime_type": headers.get("Content-Type", "application/octet-stream"),
            "data_base64": base64.b64encode(body).decode("ascii"),
            "size": len(body),
        }
