import email.utils
import os
import random
import re
import time
from datetime import datetime, timezone

from dotenv import load_dotenv
from groq import (
    Groq,
    APIConnectionError,
    APITimeoutError,
    APIStatusError,
)

load_dotenv()

API_KEY = os.getenv("GROQ_API_KEY")
MODEL = os.getenv("GROQ_MODEL", "openai/gpt-oss-120b")
TIMEOUT = float(os.getenv("GROQ_TIMEOUT", "45"))

MAX_ATTEMPTS = int(os.getenv("GROQ_MAX_ATTEMPTS", "3"))
INITIAL_BACKOFF_SECONDS = float(os.getenv("GROQ_INITIAL_BACKOFF", "2"))
MAX_BACKOFF_SECONDS = float(os.getenv("GROQ_MAX_BACKOFF", "30"))
MAX_SERVER_WAIT_SECONDS = float(os.getenv("GROQ_MAX_SERVER_WAIT", "30"))

if not API_KEY:
    raise ValueError("GROQ_API_KEY is missing from .env")

client = Groq(
    api_key=API_KEY,
    timeout=TIMEOUT,
    max_retries=0,  # Disable SDK retries; this module owns the retry policy.
)


class GroqRequestError(RuntimeError):
    """A controlled API failure after the configured retry policy is applied."""

    def __init__(self, message, *, category, status=None,
                 attempts=1, retry_count=0, details=None):
        super().__init__(message)
        self.category = category
        self.status = status
        self.attempts = attempts
        self.retry_count = retry_count
        self.details = details or {}


def _response_headers(error):
    response = getattr(error, "response", None)
    if response is None:
        return {}
    try:
        return {str(k).lower(): str(v) for k, v in response.headers.items()}
    except Exception:
        return {}


def get_rate_limit_details(error):
    headers = _response_headers(error)
    return {
        "status": getattr(error, "status_code", None),
        "message": str(error),
        "retry_after": headers.get("retry-after"),
        "limit_tokens": headers.get("x-ratelimit-limit-tokens"),
        "remaining_tokens": headers.get("x-ratelimit-remaining-tokens"),
        "reset_tokens": headers.get("x-ratelimit-reset-tokens"),
        "limit_requests": headers.get("x-ratelimit-limit-requests"),
        "remaining_requests": headers.get("x-ratelimit-remaining-requests"),
        "reset_requests": headers.get("x-ratelimit-reset-requests"),
    }


def _parse_duration(value):
    """Parse seconds or compact values such as '1m2s'/'250ms'."""
    if value is None:
        return None
    raw = str(value).strip().lower()
    try:
        return max(0.0, float(raw))
    except ValueError:
        pass

    matches = re.findall(r"(\d+(?:\.\d+)?)\s*(ms|s|m|h)", raw)
    if not matches:
        return None
    units = {"ms": 0.001, "s": 1, "m": 60, "h": 3600}
    return sum(float(number) * units[unit] for number, unit in matches)


def _server_retry_delay(details):
    retry_after = details.get("retry_after")
    delay = _parse_duration(retry_after)
    if delay is not None:
        return delay

    # Retry-After can also be an HTTP date.
    if retry_after:
        try:
            dt = email.utils.parsedate_to_datetime(retry_after)
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
            return max(0.0, (dt - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            pass

    return _parse_duration(details.get("reset_tokens")) or _parse_duration(
        details.get("reset_requests")
    )


def _is_daily_token_exhaustion(details):
    message = (details.get("message") or "").lower()
    explicit_daily_markers = (
        "tokens per day", "token per day", "daily token",
        "daily limit", "per-day", "(tpd)", " tpd",
    )
    return any(marker in message for marker in explicit_daily_markers)


def _classify_error(error):
    details = get_rate_limit_details(error)
    status = details.get("status")

    if status == 429:
        if _is_daily_token_exhaustion(details):
            return "daily_quota_exhausted", False, details

        # A normal RPM/TPM 429 may recover after the provider's reset window.
        server_delay = _server_retry_delay(details)
        if server_delay is None:
            return "rate_limited_no_reset", False, details
        if server_delay > MAX_SERVER_WAIT_SECONDS:
            return "rate_limited_deferred", False, details
        return "transient_rate_limit", True, details

    if status in (500, 502, 503, 504):
        return "server_error", True, details

    if isinstance(error, (APITimeoutError, APIConnectionError)):
        return "connection_or_timeout", True, details

    if status in (401, 403):
        return "authentication_error", False, details

    if status is not None and 400 <= status < 500:
        return "client_error", False, details

    return "unexpected_error", False, details


def _backoff_seconds(attempt_number, details, category):
    if category == "transient_rate_limit":
        server_delay = _server_retry_delay(details)
        if server_delay is not None:
            return server_delay + random.uniform(0.0, 0.25)

    base = min(
        MAX_BACKOFF_SECONDS,
        INITIAL_BACKOFF_SECONDS * (2 ** (attempt_number - 1))
    )
    return base + random.uniform(0.0, min(1.0, base * 0.25))


def generate_completion(messages, max_tokens=1200, temperature=0.0):
    """
    Generate one completion with bounded, observable retries.

    Daily token quota exhaustion is not retried automatically. Transient
    429s are retried only when the provider supplies a usable reset delay.
    """
    start = time.perf_counter()
    retry_count = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            response = client.chat.completions.create(
                model=MODEL,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_tokens,
            )

            elapsed_ms = (time.perf_counter() - start) * 1000
            message = response.choices[0].message
            content = message.content or ""
            usage = response.usage

            # Log metadata only. Do not print model reasoning or full message objects.
            print(
                f"Groq success | attempt={attempt} | "
                f"finish_reason={response.choices[0].finish_reason} | "
                f"content_chars={len(content)} | "
                f"tokens_in={usage.prompt_tokens if usage else 0} | "
                f"tokens_out={usage.completion_tokens if usage else 0}"
            )

            return {
                "content": content,
                "finish_reason": response.choices[0].finish_reason,
                "model": response.model,
                "latency_ms": elapsed_ms,
                "tokens_in": usage.prompt_tokens if usage else 0,
                "tokens_out": usage.completion_tokens if usage else 0,
                "total_tokens": usage.total_tokens if usage else 0,
                "api_attempts": attempt,
                "api_retries": retry_count,
            }

        except (APIStatusError, APIConnectionError, APITimeoutError) as error:
            category, retryable, details = _classify_error(error)
            status = details.get("status")

            print(
                f"Groq API failure | attempt={attempt}/{MAX_ATTEMPTS} | "
                f"category={category} | status={status} | "
                f"message={details.get('message', '')[:500]}"
            )

            if not retryable or attempt >= MAX_ATTEMPTS:
                raise GroqRequestError(
                    f"Groq request stopped: {category}",
                    category=category,
                    status=status,
                    attempts=attempt,
                    retry_count=retry_count,
                    details=details,
                ) from error

            delay = _backoff_seconds(attempt, details, category)
            print(f"Retrying Groq request in {delay:.2f} seconds.")
            time.sleep(delay)
            retry_count += 1

        except Exception as error:
            # Do not retry programming errors or unknown failures blindly.
            raise GroqRequestError(
                f"Unexpected Groq client failure: {type(error).__name__}",
                category="unexpected_error",
                attempts=attempt,
                retry_count=retry_count,
                details={"message": str(error)[:500]},
            ) from error

    # Defensive fallback; loop normally returns or raises.
    raise GroqRequestError(
        "Groq request exhausted attempts.",
        category="attempts_exhausted",
        attempts=MAX_ATTEMPTS,
        retry_count=retry_count,
    )