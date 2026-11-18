"""Explicit environment credentials and one non-generative model access check.

No dotenv, key files, credential fallback, generation requests, or disk writes.
Official references (checked 2026-10-28):
https://developers.openai.com/api/reference/python/resources/models/methods/retrieve
https://platform.claude.com/docs/en/api/python/models/retrieve
https://platform.claude.com/docs/en/api/errors
"""

from __future__ import annotations

from contextlib import contextmanager
import os
import sys


KEY_NAMES = {"openai": "OPENAI_API_KEY", "anthropic": "ANTHROPIC_API_KEY"}
OFFICIAL_URLS = {"openai": "https://api.openai.com/v1", "anthropic": "https://api.anthropic.com"}
QUOTA_CODES = {
    "insufficient_quota",
    "insufficient_credits",
    "credit_balance_too_low",
    "billing_error",
    "billing_hard_limit_reached",
    "usage_limit_reached",
    "workspace_spend_limit",
    "organization_spend_limit",
    "spend_limit_exceeded",
}


class AuthenticationCheckError(RuntimeError):
    """Contains only a fixed, safe diagnostic; never the SDK exception text."""

    def __init__(self, category: str, message: str, *, status: int | None = None):
        super().__init__(message)
        self.category = category
        self.status = status
        self.exit_code = 78 if category == "authentication" else 1


def key_from_environment(provider: str) -> str:
    if provider not in KEY_NAMES:
        raise AuthenticationCheckError("configuration", "Unsupported API provider.")
    name = KEY_NAMES[provider]
    key = os.environ.get(name, "")
    if not key or key in {"your-openai-api-key-here", "your-anthropic-api-key-here"}:
        raise AuthenticationCheckError(
            "missing_key", f"{name} is missing. In this terminal, run: source ./keys.sh {provider}"
        )
    if any(character.isspace() or ord(character) < 33 or ord(character) > 126 for character in key):
        raise AuthenticationCheckError(
            "invalid_key_format",
            f"{name} contains whitespace or an invalid character. Run: source ./keys.sh {provider} --replace",
        )
    return key


@contextmanager
def _without_anthropic_overrides():
    """Prevent token/custom-header auth, then restore the caller's environment."""
    names = ("ANTHROPIC_AUTH_TOKEN", "ANTHROPIC_CUSTOM_HEADERS")
    saved = {name: os.environ.get(name) for name in names}
    try:
        for name in names:
            os.environ[name] = ""
        yield
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def make_client(timeout: float, provider: str = "openai"):
    """Construct a client only. No requests or retries occur here."""
    key = key_from_environment(provider)
    common = {
        "api_key": key,
        "base_url": OFFICIAL_URLS[provider],
        "max_retries": 0,
        "timeout": timeout,
        "default_headers": {},
    }
    try:
        if provider == "openai":
            from openai import OpenAI

            return OpenAI(**common)
        from anthropic import Anthropic

        with _without_anthropic_overrides():
            return Anthropic(**common, auth_token="", open_telemetry=False)
    except ImportError:
        raise AuthenticationCheckError(
            "dependency", "Could not import the SDK. Run: python -m pip install -r requirements.txt"
        ) from None
    except Exception:
        raise AuthenticationCheckError(
            "client_setup",
            "Could not create an API client. Check the installed SDK and runtime environment.",
        ) from None


def _quota_error(error: Exception) -> bool:
    body = getattr(error, "body", None)
    if not isinstance(body, dict):
        return False
    nested = body.get("error", body)
    if not isinstance(nested, dict):
        return False
    details = nested.get("details", {})
    values = [nested.get("type"), nested.get("code")]
    if isinstance(details, dict):
        values.append(details.get("error_code"))
    return any(isinstance(value, str) and value in QUOTA_CODES for value in values)


def safe_error(error: Exception, provider: str) -> AuthenticationCheckError:
    """Classify HTTP status/allowlisted codes without emitting bodies or keys."""
    status = getattr(error, "status_code", None)
    status = status if type(status) is int else None
    if status == 401:
        return AuthenticationCheckError(
            "authentication",
            f"Authentication failed (HTTP 401). Replace the key with source ./keys.sh {provider} --replace and try again.",
            status=status,
        )
    if status == 403:
        return AuthenticationCheckError(
            "permission",
            "Access denied (HTTP 403). Check the API key permissions and organization or project access.",
            status=status,
        )
    if status == 404:
        return AuthenticationCheckError(
            "model_access",
            "Model lookup failed (HTTP 404). Check the model ID and whether this key has access to it.",
            status=status,
        )
    if status == 402 or _quota_error(error):
        return AuthenticationCheckError(
            "billing",
            "Billing, credit or usage limit reached. Check the balance and spending limits in the provider console.",
            status=status,
        )
    if status == 429:
        return AuthenticationCheckError(
            "rate_or_usage_limit",
            "Rate or usage limit reached (HTTP 429). Check rate and spending limits in the provider console before retrying.",
            status=status,
        )
    if status is not None:
        return AuthenticationCheckError(
            "http",
            f"The provider rejected the model lookup (HTTP {status}). No automatic retry was attempted.",
            status=status,
        )
    if type(error).__name__ in {
        "APIConnectionError",
        "APITimeoutError",
        "ConnectError",
        "ConnectTimeout",
        "ReadTimeout",
        "TimeoutException",
        "NetworkError",
    } or isinstance(error, (ConnectionError, TimeoutError)):
        return AuthenticationCheckError(
            "network", "API connection failed or timed out. Check the network and try again."
        )
    return AuthenticationCheckError(
        "check_failed", "Could not complete the model lookup. Check the SDK and network connection."
    )


def check_model_access(client, provider: str, model: str) -> None:
    """Exactly one GET model request; no inference or experimental conversation."""
    try:
        if provider == "anthropic":
            info = client.models.retrieve(model_id=model)
        else:
            info = client.models.retrieve(model)
    except Exception as error:
        raise safe_error(error, provider) from None
    identifier = info.get("id") if isinstance(info, dict) else getattr(info, "id", None)
    # Aliases may resolve to a dated ID. Do not replace the configured model.
    if not isinstance(identifier, str) or not identifier:
        raise AuthenticationCheckError(
            "invalid_response", "Invalid model lookup response. The experiment has not started."
        )


def checked_client(config: dict):
    """Return the same authenticated client that will perform the experiment."""
    client = make_client(config["timeout"], config["provider"])
    try:
        check_model_access(client, config["provider"], config["model"])
    except BaseException:
        client.close()
        raise
    print(
        "Authentication and model lookup succeeded. Generation is still subject to available credit, permissions and limits.",
        file=sys.stderr,
        flush=True,
    )
    return client
