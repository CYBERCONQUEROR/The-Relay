
import asyncio
import random
import time
from collections import deque
from datetime import datetime, timezone
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from simulator.simulator_config import CONFIG

from pydantic import BaseModel


app = FastAPI(
    title="The Relay - Stand-in LLM Service",
    description="A configurable simulator for hosted LLM API behavior.",
    version="1.0.0"
)

# Request records: (timestamp, estimated_tokens)
request_history = deque()

daily_tokens_used = 0
total_requests = 0
total_failures = 0

active_requests = 0
state_lock = asyncio.Lock()
concurrency_semaphore = asyncio.Semaphore(
    CONFIG.max_concurrent_requests
)


class SimulationRequest(BaseModel):
    prompt: str = Field(min_length=1, max_length=10000)
    max_tokens: int = Field(default=250, ge=1, le=4000)
    scenario: str = "manual"
class ConfigurationUpdate(BaseModel):
    requests_per_minute: Optional[int] = None
    tokens_per_minute: Optional[int] = None
    daily_token_budget: Optional[int] = None
    base_latency_ms: Optional[int] = None
    latency_jitter_ms: Optional[int] = None
    failure_probability: Optional[float] = None
    timeout_probability: Optional[float] = None
    degradation_start_ratio: Optional[float] = None
    max_degradation_multiplier: Optional[float] = None

def estimate_tokens(text: str) -> int:
    """Approximate token count for simulation only."""
    return max(1, len(text) // 4)


def cleanup_old_requests(now: float):
    """Remove requests outside the rolling 60-second window."""
    while request_history and now - request_history[0][0] >= 60:
        request_history.popleft()


def current_minute_usage():
    return {
        "requests": len(request_history),
        "tokens": sum(item[1] for item in request_history)
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
    global daily_tokens_used, total_requests

    now = time.monotonic()
    required_tokens = input_tokens + output_tokens

    async with state_lock:
        cleanup_old_requests(now)

        usage = current_minute_usage()

        if usage["requests"] + 1 > CONFIG.requests_per_minute:
            raise HTTPException(
                status_code=429,
                detail="Simulated request-per-minute limit exceeded."
            )

        if usage["tokens"] + required_tokens > CONFIG.tokens_per_minute:
            raise HTTPException(
                status_code=429,
                detail="Simulated tokens-per-minute limit exceeded."
            )

        if daily_tokens_used + required_tokens > CONFIG.daily_token_budget:
            raise HTTPException(
                status_code=429,
                detail="Simulated daily token budget exhausted."
            )

        request_history.append((now, required_tokens))
        daily_tokens_used += required_tokens
        total_requests += 1

        return usage["tokens"] + required_tokens


@app.get("/health")
async def health():
    return {
        "status": "healthy",
        "service": "The Relay Stand-in",
        "time": datetime.now(timezone.utc).isoformat()
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
            "active_requests": active_requests,
            "requests_last_60_seconds": usage["requests"],
            "tokens_last_60_seconds": usage["tokens"],
            "daily_tokens_used": daily_tokens_used,
            "daily_token_budget": CONFIG.daily_token_budget,
            "rpm_limit": CONFIG.requests_per_minute,
            "tpm_limit": CONFIG.tokens_per_minute
        }


@app.post("/reset")
async def reset_simulator():
    global daily_tokens_used, total_requests, total_failures

    async with state_lock:
        request_history.clear()
        daily_tokens_used = 0
        total_requests = 0
        total_failures = 0

    return {"message": "Simulator counters reset."}


@app.post("/configure")
async def configure_simulator(update: ConfigurationUpdate):
    for key, value in update.model_dump(exclude_none=True).items():
        if key in ("failure_probability", "timeout_probability"):
            if not 0 <= value <= 1:
                raise HTTPException(400, f"{key} must be between 0 and 1")

        if key in (
            "requests_per_minute",
            "tokens_per_minute",
            "daily_token_budget"
        ) and value < 1:
            raise HTTPException(400, f"{key} must be at least 1")

        setattr(CONFIG, key, value)

    return {
        "message": "Configuration updated",
        "configuration": CONFIG.__dict__
    }



@app.post("/simulate")
async def simulate(request: SimulationRequest):
    global active_requests, total_failures

    input_tokens = estimate_tokens(request.prompt)
    output_tokens = request.max_tokens

    # Reserve quota before doing simulated work.
    load_tokens = await reserve_quota(input_tokens, output_tokens)

    # Simulate concurrency constraints.
    async with concurrency_semaphore:
        async with state_lock:
            active_requests += 1

        try:
            # Inject a server failure.
            if random.random() < CONFIG.failure_probability:
                async with state_lock:
                    total_failures += 1

                raise HTTPException(
                    status_code=503,
                    detail="Injected simulated server failure."
                )

            # Inject a timeout.
            if random.random() < CONFIG.timeout_probability:
                await asyncio.sleep(10)

                async with state_lock:
                    total_failures += 1

                raise HTTPException(
                    status_code=504,
                    detail="Injected simulated timeout."
                )

            multiplier = calculate_load_multiplier(load_tokens)

            jitter = random.uniform(
                -CONFIG.latency_jitter_ms,
                CONFIG.latency_jitter_ms
            )

            latency_ms = max(
                50,
                (CONFIG.base_latency_ms + jitter) * multiplier
            )

            start = time.perf_counter()
            await asyncio.sleep(latency_ms / 1000)
            total_ms = (time.perf_counter() - start) * 1000

            simulated_answer = (
                "This is a simulated response from The Relay stand-in "
                "service. It is not a real answer generated by Groq."
            )

            return {
                "status": "ok",
                "scenario": request.scenario,
                "answer": simulated_answer,
                "input_tokens": input_tokens,
                "output_tokens": output_tokens,
                "total_tokens": input_tokens + output_tokens,
                "latency_ms": round(total_ms, 2),
                "load_multiplier": round(multiplier, 2),
                "source": "standin"
            }

        finally:
            async with state_lock:
                active_requests -= 1


@app.post("/simulate/stream")
async def simulate_stream(request: SimulationRequest):
    """
    Simulates time to first token and subsequent token delivery.
    The stream is synthetic; it does not call a real language model.
    """
    input_tokens = estimate_tokens(request.prompt)
    output_tokens = request.max_tokens

    load_tokens = await reserve_quota(input_tokens, output_tokens)
    multiplier = calculate_load_multiplier(load_tokens)

    async def event_generator():
        global total_failures

        async with concurrency_semaphore:
            async with state_lock:
                global active_requests
                active_requests += 1

            try:
                if random.random() < CONFIG.failure_probability:
                    async with state_lock:
                        total_failures += 1

                    yield "event: error\ndata: simulated_server_failure\n\n"
                    return

                if random.random() < CONFIG.timeout_probability:
                    await asyncio.sleep(10)

                    async with state_lock:
                        total_failures += 1

                    yield "event: error\ndata: simulated_timeout\n\n"
                    return

                first_token_delay = (
                    CONFIG.base_latency_ms * 0.35 * multiplier
                )

                await asyncio.sleep(first_token_delay / 1000)

                words = [
                    "This", "is", "a", "simulated", "stream",
                    "from", "The", "Relay", "stand-in", "service."
                ]

                for word in words:
                    yield f"data: {word}\n\n"
                    await asyncio.sleep(0.08 * multiplier)

                yield "event: done\ndata: completed\n\n"

            finally:
                async with state_lock:
                    active_requests -= 1

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream"
    )
