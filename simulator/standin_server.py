import asyncio
import random
import time
from collections import deque
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from simulator.simulator_config import CONFIG


app = FastAPI(
    title="The Relay - Stand-in LLM Service",
    description="A configurable simulator for hosted LLM API behavior.",
    version="1.1.0",
)

# Rolling-minute records: (monotonic timestamp, estimated total tokens).
request_history = deque()

daily_tokens_used = 0
daily_requests_used = 0
total_requests = 0
total_failures = 0
total_timeouts = 0
total_queue_rejections = 0

active_requests = 0
queued_requests = 0
peak_queue_length = 0

state_lock = asyncio.Lock()
worker_semaphore = asyncio.Semaphore(CONFIG.max_concurrent_requests)


class SimulationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)
    max_tokens: int = Field(default=250, ge=1, le=4000)
    scenario: str = "manual"
    deadline_seconds: Optional[float] = Field(default=None, gt=0, le=120)


class ConfigurationUpdate(BaseModel):
    requests_per_minute: Optional[int] = None
    tokens_per_minute: Optional[int] = None
    requests_per_day: Optional[int] = None
    daily_token_budget: Optional[int] = None
    base_latency_ms: Optional[int] = None
    latency_jitter_ms: Optional[int] = None
    max_concurrent_requests: Optional[int] = None
    max_queue_size: Optional[int] = None
    request_deadline_seconds: Optional[float] = None
    failure_probability: Optional[float] = None
    timeout_probability: Optional[float] = None
    degradation_start_ratio: Optional[float] = None
    max_degradation_multiplier: Optional[float] = None


def estimate_tokens(text: str) -> int:
    """Approximate token count for simulation only."""
    return max(1, len(text) // 4)


def cleanup_old_requests(now: float):
    while request_history and now - request_history[0][0] >= 60:
        request_history.popleft()


def current_minute_usage():
    return {
        "requests": len(request_history),
        "tokens": sum(item[1] for item in request_history),
    }


def calculate_load_multiplier(token_usage: int) -> float:
    limit = max(CONFIG.tokens_per_minute, 1)
    load_ratio = token_usage / limit
    if load_ratio <= CONFIG.degradation_start_ratio:
        return 1.0

    excess = load_ratio - CONFIG.degradation_start_ratio
    available_range = max(1.0 - CONFIG.degradation_start_ratio, 0.01)
    multiplier = 1 + (
        excess / available_range
    ) * (CONFIG.max_degradation_multiplier - 1)
    return min(multiplier, CONFIG.max_degradation_multiplier)


async def reserve_quota(input_tokens: int, output_tokens: int):
    """Atomically reserve simulated RPM, TPM, RPD and TPD capacity."""
    global daily_tokens_used, daily_requests_used, total_requests

    now = time.monotonic()
    required_tokens = input_tokens + output_tokens

    async with state_lock:
        cleanup_old_requests(now)
        usage = current_minute_usage()

        if usage["requests"] + 1 > CONFIG.requests_per_minute:
            raise HTTPException(429, "Simulated RPM limit exceeded.")
        if usage["tokens"] + required_tokens > CONFIG.tokens_per_minute:
            raise HTTPException(429, "Simulated TPM limit exceeded.")
        if daily_requests_used + 1 > CONFIG.requests_per_day:
            raise HTTPException(429, "Simulated RPD limit exceeded.")
        if daily_tokens_used + required_tokens > CONFIG.daily_token_budget:
            raise HTTPException(429, "Simulated TPD limit exceeded.")

        request_history.append((now, required_tokens))
        daily_requests_used += 1
        daily_tokens_used += required_tokens
        total_requests += 1
        return usage["tokens"] + required_tokens


@asynccontextmanager
async def worker_slot(deadline_seconds: float):
    """
    Admit at most max_concurrent_requests + max_queue_size requests.
    Waiting requests have a bounded queue position and share the request
    deadline with service execution.
    """
    global active_requests, queued_requests, peak_queue_length
    admitted = False
    acquired = False
    started_waiting = time.monotonic()

    async with state_lock:
        capacity = CONFIG.max_concurrent_requests + CONFIG.max_queue_size
        if active_requests + queued_requests >= capacity:
            raise HTTPException(503, "Stand-in worker queue is full.")
        queued_requests += 1
        peak_queue_length = max(peak_queue_length, queued_requests)
        admitted = True

    try:
        try:
            await asyncio.wait_for(
                worker_semaphore.acquire(),
                timeout=deadline_seconds,
            )
            acquired = True
        except asyncio.TimeoutError:
            raise HTTPException(504, "Request deadline expired in queue.")

        async with state_lock:
            queued_requests -= 1
            active_requests += 1

        yield {
            "queue_wait_ms": round((time.monotonic() - started_waiting) * 1000, 2)
        }

    finally:
        if admitted and not acquired:
            async with state_lock:
                queued_requests = max(0, queued_requests - 1)
        if acquired:
            async with state_lock:
                active_requests = max(0, active_requests - 1)
            worker_semaphore.release()


async def perform_simulated_work(request: SimulationRequest, load_tokens: int):
    global total_failures, total_timeouts

    if random.random() < CONFIG.failure_probability:
        async with state_lock:
            total_failures += 1
        raise HTTPException(503, "Injected simulated server failure.")

    if random.random() < CONFIG.timeout_probability:
        await asyncio.sleep(CONFIG.request_deadline_seconds + 1)
        raise HTTPException(504, "Injected simulated service timeout.")

    multiplier = calculate_load_multiplier(load_tokens)
    jitter = random.uniform(-CONFIG.latency_jitter_ms, CONFIG.latency_jitter_ms)
    latency_ms = max(50, (CONFIG.base_latency_ms + jitter) * multiplier)
    await asyncio.sleep(latency_ms / 1000)

    input_tokens = estimate_tokens(request.prompt)
    output_tokens = request.max_tokens
    return {
        "status": "ok",
        "scenario": request.scenario,
        "answer": (
            "This is a simulated response from The Relay stand-in "
            "service. It is not a real answer generated by Groq."
        ),
        "input_tokens": input_tokens,
        "output_tokens": output_tokens,
        "total_tokens": input_tokens + output_tokens,
        "latency_ms": round(latency_ms, 2),
        "load_multiplier": round(multiplier, 2),
        "source": "standin",
    }


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "The Relay Stand-in",
        "time": datetime.now(timezone.utc).isoformat(),
    }


@app.get("/metrics")
async def metrics():
    now = time.monotonic()
    async with state_lock:
        cleanup_old_requests(now)
        usage = current_minute_usage()
        return {
            "total_requests": total_requests,
            "total_failures": total_failures,
            "total_timeouts": total_timeouts,
            "total_queue_rejections": total_queue_rejections,
            "active_requests": active_requests,
            "queued_requests": queued_requests,
            "peak_queue_length": peak_queue_length,
            "worker_capacity": CONFIG.max_concurrent_requests,
            "queue_capacity": CONFIG.max_queue_size,
            "requests_last_60_seconds": usage["requests"],
            "tokens_last_60_seconds": usage["tokens"],
            "daily_requests_used": daily_requests_used,
            "daily_request_limit": CONFIG.requests_per_day,
            "daily_tokens_used": daily_tokens_used,
            "daily_token_budget": CONFIG.daily_token_budget,
            "rpm_limit": CONFIG.requests_per_minute,
            "tpm_limit": CONFIG.tokens_per_minute,
        }


@app.post("/reset")
async def reset_simulator():
    global daily_tokens_used, daily_requests_used, total_requests
    global total_failures, total_timeouts, total_queue_rejections, peak_queue_length

    async with state_lock:
        request_history.clear()
        daily_tokens_used = 0
        daily_requests_used = 0
        total_requests = 0
        total_failures = 0
        total_timeouts = 0
        total_queue_rejections = 0
        peak_queue_length = 0

    return {"message": "Simulator counters reset."}


@app.post("/configure")
async def configure_simulator(update: ConfigurationUpdate):
    global worker_semaphore

    values = update.model_dump(exclude_none=True)
    for key, value in values.items():
        if key in ("failure_probability", "timeout_probability"):
            if not 0 <= value <= 1:
                raise HTTPException(400, f"{key} must be between 0 and 1")
        if key in (
            "requests_per_minute", "tokens_per_minute",
            "requests_per_day", "daily_token_budget",
            "max_concurrent_requests", "max_queue_size",
        ) and value < 1:
            raise HTTPException(400, f"{key} must be at least 1")
        if key in ("base_latency_ms", "latency_jitter_ms") and value < 0:
            raise HTTPException(400, f"{key} cannot be negative")
        setattr(CONFIG, key, value)

    # Reconfiguration of worker count is safe only while no requests are active.
    if "max_concurrent_requests" in values:
        async with state_lock:
            if active_requests or queued_requests:
                raise HTTPException(
                    409,
                    "Cannot change worker count while requests are active or queued.",
                )
            worker_semaphore = asyncio.Semaphore(CONFIG.max_concurrent_requests)

    return {"message": "Configuration updated", "configuration": CONFIG.__dict__}


@app.post("/simulate")
async def simulate(request: SimulationRequest):
    global total_failures, total_queue_rejections

    input_tokens = estimate_tokens(request.prompt)
    output_tokens = request.max_tokens
    load_tokens = await reserve_quota(input_tokens, output_tokens)
    deadline = request.deadline_seconds or CONFIG.request_deadline_seconds
    started = time.monotonic()

    try:
        async with worker_slot(deadline) as slot:
            remaining = deadline - (time.monotonic() - started)
            if remaining <= 0:
                raise HTTPException(504, "Request deadline expired.")
            try:
                result = await asyncio.wait_for(
                    perform_simulated_work(request, load_tokens),
                    timeout=remaining,
                )
            except asyncio.TimeoutError:
                async with state_lock:
                    total_failures += 1
                    total_timeouts += 1
                raise HTTPException(504, "Request deadline exceeded.")

            result["queue_wait_ms"] = slot["queue_wait_ms"]
            result["total_request_ms"] = round((time.monotonic() - started) * 1000, 2)
            return result
    except HTTPException as exc:
        if exc.status_code == 503 and "queue is full" in str(exc.detail):
            async with state_lock:
                total_queue_rejections += 1
        raise


@app.post("/simulate/stream")
async def simulate_stream(request: SimulationRequest):
    """
    Synthetic SSE stream. First-token time is the delay before the first data
    event; this endpoint does not call a real language model.
    """
    input_tokens = estimate_tokens(request.prompt)
    output_tokens = request.max_tokens
    load_tokens = await reserve_quota(input_tokens, output_tokens)
    deadline = request.deadline_seconds or CONFIG.request_deadline_seconds

    async def event_generator():
        global total_failures, total_timeouts
        started = time.monotonic()
        try:
            async with worker_slot(deadline):
                multiplier = calculate_load_multiplier(load_tokens)
                if random.random() < CONFIG.failure_probability:
                    async with state_lock:
                        total_failures += 1
                    yield "event: error\ndata: simulated_server_failure\n\n"
                    return
                if random.random() < CONFIG.timeout_probability:
                    async with state_lock:
                        total_timeouts += 1
                    yield "event: error\ndata: simulated_timeout\n\n"
                    return

                first_token_delay = CONFIG.base_latency_ms * 0.35 * multiplier
                await asyncio.sleep(first_token_delay / 1000)
                yield f"event: metadata\ndata: {{\"ttft_ms\": {round((time.monotonic()-started)*1000, 2)}}}\n\n"

                words = [
                    "This", "is", "a", "simulated", "stream",
                    "from", "The", "Relay", "stand-in", "service."
                ]
                for word in words:
                    yield f"data: {word}\n\n"
                    await asyncio.sleep(0.08 * multiplier)
                yield "event: done\ndata: completed\n\n"
        except HTTPException as exc:
            yield f"event: error\ndata: {exc.status_code}:{exc.detail}\n\n"
        finally:
            # worker_slot handles active/queued accounting and semaphore release.
            pass

    return StreamingResponse(event_generator(), media_type="text/event-stream")