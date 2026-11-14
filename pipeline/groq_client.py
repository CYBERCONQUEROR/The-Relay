import email.utils
import os
import random
import re
import time
import uuid
from datetime import datetime, timezone

from dotenv import load_dotenv
from reliability.telemetry import emit, sanitize, current_configuration
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

client = Groq(
    api_key=API_KEY or "missing-key",
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
        "message": sanitize(error),
        "retry_after": headers.get("retry-after"),
        "limit_tokens": headers.get("x-ratelimit-limit-tokens"),
        "remaining_tokens": headers.get("x-ratelimit-remaining-tokens"),
        "reset_tokens": headers.get("x-ratelimit-reset-tokens"),
        "limit_requests": headers.get("x-ratelimit-limit-requests"),
        "remaining_requests": headers.get("x-ratelimit-remaining-requests"),
        "reset_requests": headers.get("x-ratelimit-reset-requests"),
        "reset_tokens_day": headers.get("x-ratelimit-reset-tokens-day") or headers.get("x-ratelimit-reset-tpd"),
        "headers": {k: v for k, v in headers.items() if k.startswith("x-ratelimit-") or k == "retry-after"},
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


def _reset_epoch(value):
    if value is None:
        return None
    raw = str(value).strip()
    try:
        numeric = float(raw)
        if numeric > 1e12:
            return numeric / 1000
        if numeric > 1e9:
            return numeric
    except ValueError:
        pass
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
        return dt.replace(tzinfo=dt.tzinfo or timezone.utc).timestamp()
    except ValueError:
        pass
    delay = _parse_duration(raw)
    return time.time() + delay if delay is not None else None


def quota_reset(details):
    """Return provider-supported retry epoch; never assume a UTC midnight reset.

    For daily exhaustion, generic minute token resets are not evidence of a
    daily reset. Explicit Retry-After or a body retry duration is usable.
    """
    daily = _is_daily_token_exhaustion(details)
    specific = details.get("reset_tokens_day") if daily else None
    if specific:
        reset = _reset_epoch(specific)
        if reset is not None:
            return reset
    retry_after = details.get("retry_after")
    if retry_after is not None:
        delay = _server_retry_delay({"retry_after": retry_after})
        if delay is not None:
            return time.time() + delay
    match = re.search(r"try again in\s+([\d.hms ]+)", details.get("message", ""), re.I)
    if match:
        delay = _parse_duration(match.group(1))
        if delay is not None:
            return time.time() + delay
    if not daily:
        resets = [_reset_epoch(details.get(key)) for key in ("reset_tokens", "reset_requests")]
        resets = [value for value in resets if value is not None]
        if resets:
            return max(resets)
    return None


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
        details["reset_at"] = quota_reset(details)
        details["reset_estimated"] = False

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


def _milliseconds(value):
    return float(value) * 1000 if isinstance(value, (int, float)) else None


def generate_completion(messages, max_tokens=1200, temperature=0.0):
    """
    Generate one completion with bounded, observable retries.

    Daily token quota exhaustion is not retried automatically. Transient
    429s are retried only when the provider supplies a usable reset delay.
    """
    if not API_KEY:
        raise GroqRequestError("GROQ_API_KEY is not configured", category="authentication_error", status=401)
    model = current_configuration().get("model", MODEL)
    start = time.perf_counter()
    retry_count = 0

    for attempt in range(1, MAX_ATTEMPTS + 1):
        request_id = uuid.uuid4().hex
        attempt_started = time.perf_counter()
        emit("groq_request", "running", request_id=request_id, attempt=attempt, model=model, provider="groq")
        try:
            raw = client.chat.completions.with_raw_response.create(
                model=model,
                messages=messages,
                temperature=temperature,
                max_completion_tokens=max_tokens,
            )

            response = raw.parse()
            response_headers = {str(k).lower(): str(v) for k, v in raw.headers.items()}
            rate_limit = {k: v for k, v in response_headers.items() if k.startswith("x-ratelimit-") or k == "retry-after"}
            elapsed_ms = (time.perf_counter() - start) * 1000
            message = response.choices[0].message
            content = message.content or ""
            usage = response.usage

            emit("groq_request", "completed", request_id=request_id, provider="groq", timestamp=time.time(),
                 success=True, http_status=200, attempt=attempt, model=response.model,
                 latency_ms=(time.perf_counter() - attempt_started) * 1000,
                 tokens_in=usage.prompt_tokens if usage else None,
                 tokens_out=usage.completion_tokens if usage else None,
                 total_tokens=usage.total_tokens if usage else None, rate_limit=rate_limit,
                 ttft_ms=None, api_processing_ms=_milliseconds(getattr(usage, "total_time", None)),
                 queue_wait_ms=_milliseconds(getattr(usage, "queue_time", None)))

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
                "rate_limit": rate_limit,
                "http_status": 200,
            }

        except (APIStatusError, APIConnectionError, APITimeoutError) as error:
            category, retryable, details = _classify_error(error)
            status = details.get("status")

            emit("groq_request", "failed", request_id=request_id, provider="groq", timestamp=time.time(),
                 success=False, http_status=status, category=category, attempt=attempt,
                 latency_ms=(time.perf_counter() - attempt_started) * 1000,
                 model=model, rate_limit=details, ttft_ms=None)

            if status == 429:
                emit("quota_exhaustion" if category == "daily_quota_exhausted" else "rate_limit_detected",
                     request_id=request_id, category=category, reset_at=details.get("reset_at"))
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
            emit("api_retry", "running", request_id=request_id, category=category, delay_seconds=delay, attempt=attempt)
            time.sleep(delay)
            retry_count += 1

        except Exception as error:
            # Do not retry programming errors or unknown failures blindly.
            raise GroqRequestError(
                f"Unexpected Groq client failure: {type(error).__name__}",
                category="unexpected_error",
                attempts=attempt,
                retry_count=retry_count,
                details={"message": sanitize(error)},
            ) from error

    # Defensive fallback; loop normally returns or raises.
    raise GroqRequestError(
        "Groq request exhausted attempts.",
        category="attempts_exhausted",
        attempts=MAX_ATTEMPTS,
        retry_count=retry_count,
    )