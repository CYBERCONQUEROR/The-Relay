"""Budgeted, opt-in provider measurements; never imported as a live test.

Every physical HTTP attempt reserves a request and conservative token allowance.
Retries consume the same budgets. TTFT is unavailable (non-streaming only).
"""
import asyncio
import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import statistics
import time
from typing import Callable
import uuid

import httpx

from reliability.telemetry import sanitize

SCENARIOS = ('baseline','low_concurrency','moderate_concurrency','rate_limit_observation','recovery')
FIELDS = ('provider','request_id','scenario','timestamp','http_status','success','error_category',
          'input_tokens','output_tokens','total_tokens','ttft_ms','latency_ms','retry_count',
          'queue_wait_ms','api_processing_ms','rate_limit_headers','retry_after','reset_tokens','reset_requests',
          'reset_tokens_day','model','completed_after_retry','observed_concurrency')
PROMPT = 'In one short sentence, describe the purpose of attention in a Transformer.'


@dataclass
class LoadConfig:
    provider: str
    scenario: str
    max_requests: int
    concurrency: int
    max_tokens: int
    max_runtime_seconds: float
    output_tokens: int = 64
    dry_run: bool = True
    confirmed: bool = False

    def validate(self):
        if self.provider not in ('groq','standin') or self.scenario not in SCENARIOS:
            raise ValueError('Invalid provider or scenario')
        if min(self.max_requests, self.concurrency, self.max_tokens, self.max_runtime_seconds, self.output_tokens) <= 0:
            raise ValueError('All limits must be positive')
        if self.provider == 'groq':
            caps = (int(os.getenv('GROQ_LOAD_TEST_MAX_REQUESTS', '20')),
                    int(os.getenv('GROQ_LOAD_TEST_MAX_CONCURRENCY', '2')),
                    int(os.getenv('GROQ_LOAD_TEST_MAX_TOKENS', '10000')),
                    float(os.getenv('GROQ_LOAD_TEST_MAX_RUNTIME_SECONDS', '300')))
            if any(v > cap for v, cap in zip((self.max_requests,self.concurrency,self.max_tokens,self.max_runtime_seconds), caps)):
                raise ValueError('Requested limits exceed backend Groq safety limits')
            if self.output_tokens > 256:
                raise ValueError('Groq output cap must not exceed 256 tokens')
            if not self.dry_run and (os.getenv('GROQ_LOAD_TEST_ENABLED','false').lower() != 'true' or not self.confirmed):
                raise ValueError('Real Groq tests require GROQ_LOAD_TEST_ENABLED=true and explicit confirmation')
            if not self.dry_run and not os.getenv('GROQ_API_KEY'):
                raise ValueError('GROQ_API_KEY is not configured')
        elif self.max_requests > 1000 or self.concurrency > 100 or self.max_tokens > 1_000_000 or self.max_runtime_seconds > 600:
            raise ValueError('Stand-in safety limits: 1000 requests, 100 concurrency, 1000000 tokens, 600 seconds')

    def summary(self):
        return {**asdict(self), 'reserved_tokens_per_attempt': len(PROMPT.encode('utf-8')) + 256 + self.output_tokens,
                'note': 'Non-streaming direct provider calls, not full RAG. Each retry counts toward all budgets. No automatic restart.'}


def percentile(values, fraction):
    if not values:
        return None
    values = sorted(values)
    i = (len(values)-1)*fraction
    lo = int(i)
    return values[lo] + (values[min(lo+1,len(values)-1)]-values[lo])*(i-lo)


def summarize(rows):
    latencies = [r['latency_ms'] for r in rows if r.get('latency_ms') is not None]
    waits = [r['queue_wait_ms'] for r in rows if r.get('queue_wait_ms') is not None]
    successes = sum(bool(r['success']) for r in rows)
    def average(key):
        values = [r[key] for r in rows if r.get(key) is not None]
        return statistics.mean(values) if values else None
    return {'provider': rows[0]['provider'] if rows else None, 'total_requests': len(rows),
            'successful_requests': successes, 'failed_requests': len(rows)-successes,
            'rate_limited_requests': sum(r.get('http_status') == 429 for r in rows),
            'success_percent': successes/len(rows)*100 if rows else None,
            'failure_percent': (len(rows)-successes)/len(rows)*100 if rows else None,
            'average_latency_ms': statistics.mean(latencies) if latencies else None,
            'median_latency_ms': percentile(latencies,.5), 'p95_latency_ms': percentile(latencies,.95),
            'p99_latency_ms': percentile(latencies,.99), 'average_input_tokens': average('input_tokens'),
            'average_output_tokens': average('output_tokens'), 'total_tokens_consumed': sum(r.get('total_tokens') or 0 for r in rows),
            'token_usage_complete': all(r.get('total_tokens') is not None for r in rows),
            'average_retries': average('retry_count'),
            'maximum_observed_concurrency': max((r.get('observed_concurrency',0) for r in rows), default=0),
            'average_queue_wait_ms': statistics.mean(waits) if waits else None, 'p95_queue_wait_ms': percentile(waits,.95)}


def write_csv(rows, results: Path, summary: Path):
    results.parent.mkdir(parents=True, exist_ok=True)
    with results.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: json.dumps(value) if isinstance(value,dict) else value for key,value in row.items() if key in FIELDS})
    report = summarize(rows)
    with summary.open('w', newline='', encoding='utf-8') as f:
        writer = csv.DictWriter(f, fieldnames=list(report))
        writer.writeheader()
        writer.writerow(report)


class LoadRunner:
    def __init__(self, config: LoadConfig, on_row: Callable | None = None, transport=None, should_stop: Callable | None = None):
        config.validate()
        self.config, self.on_row, self.transport = config, on_row, transport
        self.should_stop = should_stop or (lambda: False)
        self.stop_event = asyncio.Event()
        self.rows = []
        self.reserved_requests = self.reserved_tokens = self.active = 0
        self.stop_reason = None
        self.deadline = 0.0
        self.lock = asyncio.Lock()
        self.not_before = 0.0
        self.retry_number = 0

    def stop(self):
        self.stop_reason = 'operator_stop'
        self.stop_event.set()

    async def _attempt(self, client):
        config = self.config
        allowance = len(PROMPT.encode('utf-8')) + 256 + config.output_tokens
        async with self.lock:
            if self.stop_event.is_set() or self.should_stop() or time.monotonic() >= self.deadline:
                return False
            if self.not_before > time.monotonic():
                return False
            if self.reserved_requests >= config.max_requests or self.reserved_tokens + allowance > config.max_tokens:
                self.stop_reason = self.stop_reason or 'request_or_token_budget'
                self.stop_event.set()
                return False
            self.reserved_requests += 1
            self.reserved_tokens += allowance
            self.active += 1
            concurrency = self.active
            retry_count = self.retry_number
        started = time.perf_counter()
        row = dict.fromkeys(FIELDS)
        row.update(provider=config.provider, request_id=str(uuid.uuid4()), scenario=config.scenario,
                   timestamp=datetime.now(timezone.utc).isoformat(), success=False, model=os.getenv('GROQ_MODEL','openai/gpt-oss-120b'),
                   retry_count=retry_count, completed_after_retry=False, observed_concurrency=concurrency)
        headers = {}
        try:
            remaining = max(.001, self.deadline-time.monotonic())
            if config.provider == 'groq':
                response = await asyncio.wait_for(client.post('https://api.groq.com/openai/v1/chat/completions',
                    headers={'Authorization': 'Bearer '+os.environ['GROQ_API_KEY']},
                    json={'model': row['model'], 'messages':[{'role':'user','content':PROMPT}],
                          'max_completion_tokens': config.output_tokens, 'temperature':0}), timeout=remaining)
            else:
                response = await asyncio.wait_for(client.post(os.getenv('STANDIN_URL','http://127.0.0.1:8002')+'/simulate',
                    json={'prompt':PROMPT,'max_tokens':config.output_tokens,'scenario':config.scenario}), timeout=remaining)
            headers = {k:v for k,v in response.headers.items() if k.startswith('x-ratelimit-') or k=='retry-after'}
            row['http_status'] = response.status_code
            try:
                body = response.json()
            except ValueError:
                body = {}
            row['success'] = 200 <= response.status_code < 300
            if row['success']:
                usage = body.get('usage') or {}
                if config.provider == 'groq':
                    row.update(input_tokens=usage.get('prompt_tokens'), output_tokens=usage.get('completion_tokens'), total_tokens=usage.get('total_tokens'),
                               queue_wait_ms=usage['queue_time']*1000 if usage.get('queue_time') is not None else None)
                else:
                    row.update(input_tokens=body.get('input_tokens'),output_tokens=body.get('output_tokens'),total_tokens=body.get('total_tokens'), queue_wait_ms=body.get('queue_wait_ms'))
                row['completed_after_retry'] = retry_count > 0
                row['api_processing_ms'] = (body.get('usage') or {}).get('total_time')
                if row['api_processing_ms'] is not None:
                    row['api_processing_ms'] *= 1000
                if config.provider == 'standin':
                    row['api_processing_ms'] = body.get('latency_ms')
                self.retry_number = 0
            else:
                message = sanitize(body.get('error', {}).get('message', '') if isinstance(body.get('error'), dict) else body.get('detail', ''))
                daily = any(s in message.lower() for s in ('tokens per day','daily','tpd','rpd','requests per day'))
                row['error_category'] = 'daily_quota_exhausted' if daily else 'rate_limited' if response.status_code == 429 else 'server_error' if response.status_code >= 500 else 'client_error'
                if daily or response.status_code in (401,403) or 400 <= response.status_code < 500 and response.status_code != 429:
                    self.stop_reason = row['error_category']
                    self.stop_event.set()
                elif response.status_code == 429:
                    # Observe once, then stop. Only the explicit recovery scenario
                    # may wait for a provider-supplied short reset, up to 3 attempts.
                    try:
                        delay = float(headers['retry-after'])
                    except (KeyError, ValueError):
                        delay = None
                    if config.scenario == 'recovery' and delay is not None and 0 <= delay <= 30 and self.retry_number < 2 and time.monotonic()+delay < self.deadline:
                        self.not_before = time.monotonic() + max(delay,.1)
                        self.retry_number += 1
                    else:
                        self.stop_reason = 'rate_limit_observed'
                        self.stop_event.set()
        except (httpx.HTTPError, asyncio.TimeoutError) as error:
            row['error_category'] = 'timeout' if isinstance(error,(asyncio.TimeoutError,httpx.TimeoutException)) else 'connection_error'
        finally:
            row['latency_ms'] = (time.perf_counter()-started)*1000
            row.update(rate_limit_headers=headers, retry_after=headers.get('retry-after'),reset_tokens=headers.get('x-ratelimit-reset-tokens'),
                       reset_requests=headers.get('x-ratelimit-reset-requests'),reset_tokens_day=headers.get('x-ratelimit-reset-tokens-day'))
            self.active -= 1
            self.rows.append(row)
            if self.on_row:
                self.on_row(row)
        return True

    async def run(self):
        if self.config.dry_run:
            return []
        self.deadline = time.monotonic()+self.config.max_runtime_seconds
        width = 1 if self.config.scenario in ('baseline','recovery','rate_limit_observation') else self.config.concurrency
        async with httpx.AsyncClient(timeout=30, transport=self.transport, trust_env=False) as client:
            while not self.stop_event.is_set() and not self.should_stop() and time.monotonic() < self.deadline:
                if self.not_before > time.monotonic():
                    # Short, interruptible waits; never continue after runtime expires.
                    await asyncio.sleep(min(.1, self.not_before-time.monotonic()))
                    continue
                if self.config.scenario == 'moderate_concurrency':
                    width = min(self.config.concurrency, 1 + len(self.rows)//2)
                results = await asyncio.gather(*(self._attempt(client) for _ in range(width)))
                if not any(results):
                    break
        self.stop_reason = self.stop_reason or ('operator_stop' if self.should_stop() else 'runtime_limit')
        return self.rows
