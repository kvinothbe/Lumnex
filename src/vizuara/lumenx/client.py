"""Typed wrapper around the LumenX admin API."""

from __future__ import annotations

import ssl
from typing import Any

import httpx
import truststore

from vizuara import config

# Use the OS trust store (Windows / macOS / Linux system CAs) so corporate
# SSL-inspection proxies don't break HTTPS requests from Python.
_SSL_CTX = truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT)


class LumenXClient:
    def __init__(
        self,
        base_url: str | None = None,
        admin_token: str | None = None,
        timeout: float = 30.0,
    ) -> None:
        self._base = (base_url or config.LUMENX_BASE_URL).rstrip("/")
        self._token = admin_token or config.LUMENX_ADMIN_TOKEN
        self._http = httpx.Client(
            base_url=self._base,
            headers={"X-Admin-Token": self._token},
            timeout=timeout,
            verify=_SSL_CTX,
        )

    def __enter__(self) -> "LumenXClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._http.close()

    def _get(self, path: str, **params: Any) -> Any:
        r = self._http.get(path, params=params or None)
        r.raise_for_status()
        return r.json()

    def stats(self) -> dict[str, Any]:
        return self._get("/api/admin/stats")

    def inbox(self, since: str | None = None) -> dict[str, Any]:
        return self._get("/api/admin/inbox", since=since) if since else self._get("/api/admin/inbox")

    def threads(self) -> dict[str, Any]:
        return self._get("/api/admin/threads")

    def thread(self, thread_id: str) -> dict[str, Any]:
        return self._get(f"/api/admin/threads/{thread_id}")

    def export(self) -> dict[str, Any]:
        return self._get("/api/admin/export")

    def products(self) -> dict[str, Any]:
        return self._get("/api/admin/products")

    def product(self, product_id: str) -> dict[str, Any]:
        return self._get(f"/api/admin/products/{product_id}")

    def reply(
        self,
        thread_id: str,
        text: str,
        draft_source: str = "human",
        confidence: float | None = None,
    ) -> dict[str, Any]:
        body: dict[str, Any] = {"text": text, "draft_source": draft_source}
        if confidence is not None:
            body["confidence"] = confidence
        r = self._http.post(f"/api/admin/threads/{thread_id}/reply", json=body)
        r.raise_for_status()
        return r.json()

    def mark_read(self, thread_id: str) -> dict[str, Any]:
        r = self._http.post(f"/api/admin/threads/{thread_id}/mark-read")
        r.raise_for_status()
        return r.json()
