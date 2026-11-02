"""Timeout-bound Meta Graph API client with explicit delivery classification.

The client never logs access tokens or token-bearing URLs. In particular, it
separates requests known not to have been sent from requests whose delivery
outcome cannot safely be inferred after a transport failure.
"""
from typing import Any

import httpx

from app.core.config import get_settings


class MetaAPIError(RuntimeError):
    """A Meta API error with a conservative delivery classification.

    delivery_state is one of:
      * ``not_sent``: connection was not established / request was not sent.
      * ``rejected``: Meta returned an explicit rejection response.
      * ``unknown``: Meta may have accepted the operation, but success was not
        confirmed. Callers MUST NOT blindly retry this case.

    ``retryable`` is only true when the provider explicitly rejected the
    request as rate-limited (HTTP 429). It does not mean that the client
    automatically retries the request.
    """

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = None,
        delivery_state: str = "unknown",
        retryable: bool = False,
        provider_code: str | int | None = None,
    ) -> None:
        super().__init__(message)
        if delivery_state not in {"not_sent", "rejected", "unknown"}:
            raise ValueError("Invalid Meta delivery state")
        self.status_code = status_code
        self.delivery_state = delivery_state
        self.retryable = bool(retryable)
        self.provider_code = str(provider_code)[:80] if provider_code is not None else None


def _classify_http_status(status_code: int) -> tuple[str, bool]:
    """Classify a received HTTP response without guessing about 5xx/408."""
    if status_code == 429:
        # A 429 response explicitly rejects this attempt. A later retry may be
        # safe after backoff, but only the task layer should decide to retry.
        return "rejected", True
    if 300 <= status_code < 400:
        # Redirects are disabled, so this operation was not accepted here.
        return "rejected", False
    if 400 <= status_code < 500 and status_code != 408:
        return "rejected", False
    # A gateway/server error or timeout response can occur after an upstream
    # system has performed the operation. Treat it as ambiguous.
    return "unknown", False


class MetaGraphClient:
    def __init__(self, timeout_seconds: float = 15.0) -> None:
        self.settings = get_settings()
        self.base_url = f"https://graph.facebook.com/{self.settings.meta_api_version}"
        self.timeout_seconds = timeout_seconds

    def get(
        self,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request("GET", path, token=token, params=params)

    def post(
        self,
        path: str,
        *,
        token: str,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        return self._request("POST", path, token=token, data=data)

    def _request(
        self,
        method: str,
        path: str,
        *,
        token: str | None = None,
        params: dict[str, Any] | None = None,
        data: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        if path.startswith("http") or ".." in path:
            raise ValueError("Only relative Graph API paths are allowed")

        headers = {"Authorization": f"Bearer {token}"} if token else {}
        try:
            with httpx.Client(timeout=self.timeout_seconds, follow_redirects=False) as client:
                response = client.request(
                    method,
                    f"{self.base_url}/{path.lstrip('/')}",
                    headers=headers,
                    params=params,
                    data=data,
                )
        except (httpx.ConnectTimeout, httpx.ConnectError, httpx.PoolTimeout) as exc:
            # No HTTP request reached Meta because the connection was not
            # established or no connection-pool slot was acquired.
            raise MetaAPIError(
                "Meta connection could not be established; request was not sent",
                delivery_state="not_sent",
                retryable=True,
            ) from exc
        except httpx.HTTPError as exc:
            # Read/write timeouts, resets, and protocol errors can occur after
            # the request body has reached Meta. Never label these as not sent.
            raise MetaAPIError(
                "Meta response was not confirmed; delivery outcome is unknown",
                delivery_state="unknown",
                retryable=False,
            ) from exc

        status_state, status_retryable = _classify_http_status(response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise MetaAPIError(
                "Meta Graph API returned a non-JSON response",
                status_code=response.status_code,
                delivery_state=status_state,
                retryable=status_retryable,
            ) from exc

        if response.is_error or (isinstance(payload, dict) and payload.get("error")):
            error = payload.get("error", {}) if isinstance(payload, dict) else {}
            if not isinstance(error, dict):
                error = {}
            message = str(error.get("message") or "Meta Graph API request failed")
            code = error.get("code")
            # A structured provider error is an explicit rejection unless the
            # HTTP status itself is ambiguous (408/5xx).
            delivery_state = status_state
            retryable = status_retryable
            if not response.is_error and error:
                delivery_state = "rejected"
                retryable = False
            raise MetaAPIError(
                message,
                status_code=response.status_code,
                delivery_state=delivery_state,
                retryable=retryable,
                provider_code=code,
            )

        if not 200 <= response.status_code < 300:
            raise MetaAPIError(
                "Meta Graph API returned an unexpected HTTP status",
                status_code=response.status_code,
                delivery_state=status_state,
                retryable=status_retryable,
            )

        if not isinstance(payload, dict):
            raise MetaAPIError(
                "Meta Graph API returned an unexpected response shape",
                status_code=response.status_code,
                delivery_state="unknown",
            )
        return payload
