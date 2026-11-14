"""FastAPI queue and monitoring routes, reusable with a temporary test store."""
import asyncio
from contextlib import asynccontextmanager
import hmac
import json
import os
from pathlib import Path
import time

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from fastapi.staticfiles import StaticFiles
import httpx
from pydantic import BaseModel, Field

from reliability.config import QueueConfig
from reliability.store import CapacityError, ConflictError, SQLiteQueue, STATUSES
from reliability.worker import Worker, default_pipeline

MESSAGES = {
    'pending': 'Your request has been received.',
    'processing': 'Your request is being processed.',
    'waiting_for_quota': "The provider's daily token quota appears exhausted. Your request is safely stored for a scheduled retry. Recovery has not yet been confirmed.",
    'retry_scheduled': 'The AI service is temporarily unavailable or rate-limited. Your request has been scheduled for retry.',
    'completed': 'Your answer is ready.',
    'failed': 'We could not complete your request. See the error details.',
    'cancelled': 'Your scheduled request was cancelled.',
}


class QuestionRequest(BaseModel):
    question: str = Field(min_length=1, max_length=2000)
    wait_seconds: float = Field(default=0, ge=0, le=30)


def snapshot():
    return {'setting': os.getenv('RETRIEVAL_SETTING', 'R4').upper(),
            'top_k': int(os.getenv('RETRIEVAL_TOP_K', '8')),
            'candidate_k': int(os.getenv('RETRIEVAL_CANDIDATE_K', '50')),
            'min_score': float(os.getenv('MIN_RETRIEVAL_SCORE', '.25')),
            'model': os.getenv('GROQ_MODEL', 'openai/gpt-oss-120b'), 'pipeline_version': '1'}


def present(job):
    return {**job, 'job_id': job['id'], 'message': MESSAGES[job['status']]}


def create_app(config: QueueConfig | None = None, pipeline=default_pipeline):
    config = config or QueueConfig.from_env()
    store = SQLiteQueue(config.database, config.capacity)
    worker = Worker(store, config, pipeline)

    @asynccontextmanager
    async def lifespan(app):
        task = asyncio.create_task(worker.run()) if config.inline_worker else None
        yield
        worker.stop()
        if task:
            await task
        await app.state.experiments.shutdown()

    app = FastAPI(title='The Relay', version='2.0.0', lifespan=lifespan)
    app.state.store, app.state.worker = store, worker
    origins = [s.strip() for s in os.getenv('CORS_ORIGINS', 'http://localhost:5173,http://127.0.0.1:5173').split(',') if s.strip()]
    app.add_middleware(CORSMiddleware, allow_origins=origins, allow_methods=['GET','POST'], allow_headers=['Content-Type','Authorization','Idempotency-Key','Last-Event-ID'])

    def access(request: Request, authorization: str | None = Header(default=None)):
        # Bind a local single-operator service to loopback. For any shared deployment
        # require a token; this is operator authentication, not multi-user tenancy.
        token = os.getenv('RELAY_ADMIN_TOKEN')
        if token and not hmac.compare_digest(authorization or '', 'Bearer ' + token):
            raise HTTPException(401, 'Operator token required')
        if not token and (os.getenv('RELAY_LOCAL_MODE', 'true').lower() != 'true' or not request.client or request.client.host not in ('127.0.0.1', '::1')):
            raise HTTPException(503, 'Configure RELAY_ADMIN_TOKEN for shared access')

    def find(job_id):
        job = store.get(job_id)
        if job is None:
            raise HTTPException(404, 'Job not found')
        return job

    @app.get('/health')
    def health():
        with store.connection() as db:
            db.execute('SELECT 1')
        return {'status': 'healthy', 'database': 'available', 'retrieval': 'loaded lazily on first request'}

    @app.post('/ask', dependencies=[Depends(access)])
    async def ask(body: QuestionRequest, request: Request, idempotency_key: str | None = Header(default=None)):
        if not body.question.strip():
            raise HTTPException(400, 'Question cannot be blank')
        if idempotency_key and len(idempotency_key) > 128:
            raise HTTPException(400, 'Idempotency-Key exceeds 128 characters')
        try:
            job = await asyncio.to_thread(store.submit, body.question.strip(), snapshot(), idempotency_key)
        except CapacityError as error:
            raise HTTPException(503, str(error), headers={'Retry-After': '5'})
        except ConflictError as error:
            raise HTTPException(409, str(error))
        deadline = time.monotonic() + body.wait_seconds
        while body.wait_seconds and job['status'] in ('pending','processing') and time.monotonic() < deadline:
            await asyncio.sleep(.1)
            job = await asyncio.to_thread(store.get, job['id'])
        from fastapi.responses import JSONResponse
        if job['status'] == 'completed' and body.wait_seconds:
            return {**job['result'], 'job_id': job['id'], 'job_status': job['status']}
        return JSONResponse(present(job), status_code=202 if job['status'] in ('pending','processing','waiting_for_quota','retry_scheduled') else 200,
                            headers={'Location': f"/jobs/{job['id']}"})

    @app.get('/jobs', dependencies=[Depends(access)])
    def jobs(status: str | None = None, limit: int = Query(25, ge=1, le=100), offset: int = Query(0, ge=0), search: str = Query('', max_length=128)):
        if status and status not in STATUSES:
            raise HTTPException(400, 'Unknown status')
        page = store.list(status, limit, offset, search)
        page['items'] = [present(j) for j in page['items']]
        return page

    @app.get('/jobs/{job_id}', dependencies=[Depends(access)])
    def job_details(job_id: str):
        return present(find(job_id))

    @app.post('/jobs/{job_id}/cancel', dependencies=[Depends(access)])
    def cancel(job_id: str):
        find(job_id)
        try:
            return present(store.cancel(job_id))
        except ConflictError as error:
            raise HTTPException(409, str(error))

    @app.get('/jobs/{job_id}/events', dependencies=[Depends(access)])
    async def events(job_id: str, request: Request, after: int = Query(0, ge=0), stream: bool = False,
                     last_event_id: str | None = Header(default=None)):
        find(job_id)
        try:
            after = max(after, int(last_event_id or 0))
        except ValueError:
            raise HTTPException(400, 'Invalid event cursor')
        if not stream:
            return {'items': store.events(job_id, after)}
        async def generate():
            cursor = after
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(store.events, job_id, cursor)
                for event in rows:
                    cursor = event['id']
                    yield f"id: {cursor}\nevent: lifecycle\ndata: {json.dumps(event)}\n\n"
                if rows:
                    continue  # Drain all durable events before closing a terminal stream.
                if (await asyncio.to_thread(store.get, job_id))['status'] in ('completed','failed','cancelled'):
                    break
                yield ': heartbeat\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type='text/event-stream', headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'})

    @app.get('/queue/metrics', dependencies=[Depends(access)])
    @app.get('/system/metrics', dependencies=[Depends(access)])
    def metrics():
        return store.metrics()

    @app.get('/system/health', dependencies=[Depends(access)])
    async def system_health():
        metrics = store.metrics()
        standin = {'provider': 'standin', 'status': 'unavailable'}
        try:
            async with httpx.AsyncClient(timeout=1, trust_env=False) as client:
                response = await client.get(os.getenv('STANDIN_URL', 'http://127.0.0.1:8002') + '/metrics')
                response.raise_for_status()
                standin = {'provider': 'standin', 'status': 'online', 'metrics': response.json()}
        except httpx.HTTPError:
            pass
        last = metrics['observations'][-1] if metrics['observations'] else None
        return {'database': 'available', 'worker': metrics['worker_status'],
                'groq': {'provider': 'groq', 'status': 'last_observed_success' if last and last.get('success') else 'unavailable' if last else 'not_observed',
                         'last_success': metrics['last_successful_groq_request'], 'observed': last,
                         'configured': bool(os.getenv('GROQ_API_KEY'))}, 'standin': standin}

    # Compatibility endpoint: verified result chunks, explicitly not provider token streaming.
    @app.post('/ask/stream', dependencies=[Depends(access)])
    async def ask_stream(body: QuestionRequest):
        if not body.question.strip():
            raise HTTPException(400, 'Question cannot be blank')
        try:
            job = store.submit(body.question.strip(), snapshot())
        except CapacityError as error:
            raise HTTPException(503, str(error))
        async def generate():
            yield 'data: ' + json.dumps({'job_id': job['id'], 'status': job['status']}) + '\n\n'
            while True:
                current = await asyncio.to_thread(store.get, job['id'])
                if current['status'] not in ('pending','processing'):
                    result = current.get('result') or {}
                    if current['status'] == 'completed':
                        yield 'data: ' + json.dumps({'chunk': result.get('answer', '')}) + '\n\n'
                    yield 'data: ' + json.dumps({'done': True, 'job_id': job['id'], 'status': current['status'], 'sources': result.get('sources', []), 'metrics': result.get('metrics', {})}) + '\n\n'
                    break
                yield ': heartbeat\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(generate(), media_type='text/event-stream')

    from reliability.experiments import ExperimentManager, install_routes
    app.state.experiments = ExperimentManager(store)
    install_routes(app, access)
    dist = Path(__file__).resolve().parent.parent / 'frontend' / 'dist'
    if dist.exists():
        app.mount('/', StaticFiles(directory=str(dist), html=True), name='dashboard')
    else:
        @app.get('/')
        def home():
            return {'project': 'The Relay', 'version': '2.0.0', 'dashboard': 'Run frontend: npm install && npm run dev'}
    return app
