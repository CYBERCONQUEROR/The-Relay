from dataclasses import dataclass


@dataclass
class SimulatorConfig:
    # Published free-tier reference limits for openai/gpt-oss-120b.
    # These are simulator defaults, not live Groq measurements.
    requests_per_minute: int = 30
    tokens_per_minute: int = 8_000
    requests_per_day: int = 1_000
    daily_token_budget: int = 200_000

    # Service capacity: worker slots plus a bounded waiting queue.
    max_concurrent_requests: int = 10
    max_queue_size: int = 20
    request_deadline_seconds: float = 8.0

    # Simulated service behavior.
    base_latency_ms: int = 1200
    latency_jitter_ms: int = 300

    # Failure injection.
    failure_probability: float = 0.02
    timeout_probability: float = 0.01

    # Load degradation.
    degradation_start_ratio: float = 0.70
    max_degradation_multiplier: float = 3.0

    # Simulated output.
    simulated_output_tokens: int = 250


CONFIG = SimulatorConfig()