
from dataclasses import dataclass


@dataclass
class SimulatorConfig:
    # Initial calibration values based on your observed Groq headers.
    requests_per_minute: int = 1000
    tokens_per_minute: int = 1000

    # Simulated daily budget. This is an experiment setting,
    # not a confirmed Groq daily quota.
    daily_token_budget: int = 100_000

    # Simulated service behavior.
    base_latency_ms: int = 1200
    latency_jitter_ms: int = 300
    max_concurrent_requests: int = 10

    # Failure injection.
    failure_probability: float = 0.20
    timeout_probability: float = 0.20

    # Load degradation.
    degradation_start_ratio: float = 0.70
    max_degradation_multiplier: float = 3.0

    # Simulated output.
    simulated_output_tokens: int = 250


CONFIG = SimulatorConfig()
