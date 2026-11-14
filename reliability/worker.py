"""Bounded async worker pool. Blocking RAG work runs in dedicated threads."""
import asyncio
import logging
import re
import signal
import time
import uuid
from typing import Callable

from reliability.config import QueueConfig
from reliability.store import SQLiteQueue
from reliability.telemetry import emit, sanitize, trace

RETRYABLE = {'transient_rate_limit', 'rate_limited_no_reset', 'rate_limited_deferred',
             'server_error', 'connection_or_timeout', 'daily_quota_exhausted'}


def retry_schedule(error: dict, config: QueueConfig) -> tuple[float, bool]:
    # The client parses Retry-After, explicit reset headers and body wait hints.
    details = error.get('rate_limit') or {}
    reset_at = details.get('reset_at')
    if isinstance(reset_at, (int, float)):
        return max(time.time() + 1, reset_at), bool(details.get('reset_estimated', False))
    delay = config.quota_fallback_seconds if error.get('category') == 'daily_quota_exhausted' else config.retry_seconds
    return time.time() + delay, True


def default_pipeline(question: str, metadata: dict):
    from pipeline.orchestrator import run_pipeline
    return run_pipeline(question, configuration=metadata)


class Worker:
    def __init__(self, store: SQLiteQueue, config: QueueConfig, pipeline: Callable = default_pipeline):
        self.store, self.config, self.pipeline = store, config, pipeline
        self.owner = str(uuid.uuid4())
        self.stop_event = asyncio.Event()

    async def _renew(self, job):
        while True:
            await asyncio.sleep(min(5, self.config.lease_seconds / 3))
            await asyncio.to_thread(self.store.heartbeat, self.owner)
            if not await asyncio.to_thread(self.store.renew, job['id'], job['_lease'], self.config.lease_seconds):
                return

    async def process_once(self) -> bool:
        await asyncio.to_thread(self.store.heartbeat, self.owner)
        job = await asyncio.to_thread(self.store.claim, self.owner, self.config.lease_seconds, self.config.concurrency)
        if not job:
            return False
        lease = job['_lease']
        if job['attempts'] > self.config.max_attempts:
            self.store.finish(job['id'], lease, 'failed', category='attempts_exhausted', message='Maximum job attempts reached, including interrupted attempts.')
            return True
        renew = asyncio.create_task(self._renew(job))
        def execute():
            recovery_confirmed = False
            def sink(event):
                nonlocal recovery_confirmed
                if job.get('error_category') == 'daily_quota_exhausted' and not recovery_confirmed and event['stage'] == 'groq_request' and event['state'] == 'completed':
                    recovery_confirmed = True
                    self.store.event(job['id'], {'stage':'quota_recovery','state':'completed','timestamp':time.time(), 'data':{'confirmed_by':'successful real provider response'}}, lease)
                self.store.event(job['id'], event, lease)
            with trace(sink, job['metadata']):
                emit('worker', 'running', job_id=job['id'], attempt=job['attempts'])
                return self.pipeline(job['question'], job['metadata'])
        try:
            result = await asyncio.to_thread(execute)
            # Public job results include verdicts, never raw verifier output.
            result = public_result(result)
            if result.get('status') == 'api_error':
                error = result.get('error') or {}
                category = error.get('category', 'unexpected_error')
                provider_limited = category.startswith('rate_limit') or category in ('daily_quota_exhausted','transient_rate_limit')
                if category in RETRYABLE and job['attempts'] < self.config.max_attempts:
                    next_run, estimated = retry_schedule(error, self.config)
                    status = 'waiting_for_quota' if category == 'daily_quota_exhausted' else 'retry_scheduled'
                    self.store.finish(job['id'], lease, status, result, category, error.get('message'), next_run, estimated,
                                      gate=provider_limited)
                else:
                    next_run, estimated = retry_schedule(error, self.config) if provider_limited else (None, False)
                    self.store.finish(job['id'], lease, 'failed', result, category, error.get('message', 'The provider rejected the request.'), next_run, estimated, gate=provider_limited)
            elif result.get('status') in ('generation_failed', 'invalid'):
                self.store.finish(job['id'], lease, 'failed', result, result['status'], result.get('answer'))
            else:
                # Refusals are completed pipeline decisions, not operational failures.
                self.store.event(job['id'], {'stage':'final_answer','state':'completed','timestamp':time.time(),'data':{'outcome':result.get('status')}}, lease)
                self.store.finish(job['id'], lease, 'completed', result)
        except Exception as error:
            self.store.finish(job['id'], lease, 'failed', category='pipeline_error', message=sanitize(error))
            logging.getLogger('relay').error('job_failed job_id=%s type=%s', job['id'], type(error).__name__)
        finally:
            renew.cancel()
            await asyncio.gather(renew, return_exceptions=True)
        return True

    async def _loop(self):
        while not self.stop_event.is_set():
            try:
                processed = await self.process_once()
            except Exception:
                logging.getLogger('relay').exception('worker_loop_failed')
                processed = False
            if not processed:
                try:
                    await asyncio.wait_for(self.stop_event.wait(), self.config.poll_seconds)
                except asyncio.TimeoutError:
                    pass

    async def run(self):
        logging.getLogger('relay').info('worker_started owner=%s concurrency=%s', self.owner, self.config.concurrency)
        await asyncio.gather(*(self._loop() for _ in range(self.config.concurrency)))

    def stop(self):
        # Finish already active calls while preventing new claims.
        self.stop_event.set()


def public_result(value):
    if isinstance(value, dict):
        return {key: public_result(item) for key, item in value.items() if key not in ('raw_response', 'reasoning', 'reasoning_content')}
    if isinstance(value, list):
        return [public_result(item) for item in value]
    if isinstance(value, str):
        return sanitize(value) if re.search(r'(?i)\b(?:gsk_|sk-)[a-z0-9_-]+', value) else value
    return value


async def main():
    from dotenv import load_dotenv
    load_dotenv()
    logging.basicConfig(level=logging.INFO, format='%(message)s')
    config = QueueConfig.from_env()
    worker = Worker(SQLiteQueue(config.database, config.capacity), config)
    for sig in (signal.SIGINT, signal.SIGTERM):
        asyncio.get_running_loop().add_signal_handler(sig, worker.stop)
    await worker.run()


if __name__ == '__main__':
    asyncio.run(main())
