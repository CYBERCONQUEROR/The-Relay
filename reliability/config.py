"""Validated operational settings. No credentials are persisted with jobs."""
from dataclasses import dataclass
import os


@dataclass(frozen=True)
class QueueConfig:
    database: str = 'data/relay.sqlite3'
    capacity: int = 200
    concurrency: int = 1
    poll_seconds: float = 1.0
    lease_seconds: float = 120.0
    max_attempts: int = 5
    quota_fallback_seconds: float = 86400.0
    retry_seconds: float = 30.0
    inline_worker: bool = True

    def __post_init__(self):
        for key in ('capacity', 'concurrency', 'poll_seconds', 'lease_seconds', 'max_attempts', 'quota_fallback_seconds', 'retry_seconds'):
            if getattr(self, key) <= 0:
                raise ValueError(f'{key} must be positive')
        if self.concurrency > 16:
            raise ValueError('QUEUE_WORKERS must not exceed 16')

    @classmethod
    def from_env(cls):
        return cls(
            database=os.getenv('QUEUE_DATABASE', 'data/relay.sqlite3'),
            capacity=int(os.getenv('QUEUE_CAPACITY', '200')),
            concurrency=int(os.getenv('QUEUE_WORKERS', '1')),
            poll_seconds=float(os.getenv('QUEUE_POLL_SECONDS', '1')),
            lease_seconds=float(os.getenv('QUEUE_LEASE_SECONDS', '120')),
            max_attempts=int(os.getenv('QUEUE_MAX_ATTEMPTS', '5')),
            quota_fallback_seconds=float(os.getenv('QUEUE_QUOTA_FALLBACK_SECONDS', '86400')),
            retry_seconds=float(os.getenv('QUEUE_RETRY_SECONDS', '30')),
            inline_worker=os.getenv('QUEUE_INLINE_WORKER', 'true').lower() == 'true',
        )
