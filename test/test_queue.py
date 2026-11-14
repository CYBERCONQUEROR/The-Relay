"""Offline durable queue, worker, API, and real-load budget regression tests."""
import asyncio
from concurrent.futures import ThreadPoolExecutor
import csv
import json
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import httpx
from fastapi.testclient import TestClient

from reliability.config import QueueConfig
from reliability.store import CapacityError, ConflictError, SQLiteQueue
from reliability.worker import Worker, public_result
from reliability.load_testing import LoadConfig, LoadRunner, write_csv
from reliability.api import create_app
from reliability.telemetry import emit, sanitize


class QueueTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.path = str(Path(self.temp.name)/'queue.db')
        self.store = SQLiteQueue(self.path, 10)
        self.config = QueueConfig(database=self.path, poll_seconds=.01, quota_fallback_seconds=.02, retry_seconds=.02, lease_seconds=.05, inline_worker=False)

    def test_persistence_and_idempotency(self):
        job = self.store.submit('Question', {'setting':'R4'}, 'unique')
        reopened = SQLiteQueue(self.path)
        self.assertEqual(reopened.get(job['id'])['question'],'Question')
        self.assertEqual(reopened.submit('Question',{'setting':'R4'},'unique')['id'],job['id'])
        with self.assertRaises(ConflictError):
            reopened.submit('Other',{},'unique')

    def test_atomic_capacity_and_concurrent_submissions(self):
        def submit(i):
            try:
                return self.store.submit(str(i),{})['id']
            except CapacityError:
                return None
        with ThreadPoolExecutor(max_workers=8) as pool:
            ids = list(pool.map(submit,range(25)))
        self.assertEqual(len(set(i for i in ids if i)),10)
        self.assertEqual(self.store.metrics()['total'],10)

    def test_fenced_leases_and_crash_recovery(self):
        job = self.store.submit('Question',{})
        first = self.store.claim('worker1',1,1)
        self.assertIsNone(self.store.claim('worker2',1,1))
        with self.store.connection(True) as db:
            db.execute('UPDATE jobs SET lease_until=0 WHERE id=?',(job['id'],))
        second = SQLiteQueue(self.path).claim('worker2',1,1)
        self.assertEqual(second['attempts'],2)
        self.assertFalse(self.store.finish(job['id'],first['_lease'],'completed',{'answer':'stale'}))
        self.assertTrue(self.store.finish(job['id'],second['_lease'],'completed',{'answer':'good'}))
        self.assertTrue(any(e['stage']=='worker_recovery' for e in self.store.events(job['id'])))

    def test_cancel_and_active_protection(self):
        one = self.store.submit('One',{})
        self.store.cancel(one['id'])
        self.assertEqual(self.store.get(one['id'])['status'],'cancelled')
        two = self.store.submit('Two',{})
        self.store.claim('a',1,1)
        with self.assertRaises(ConflictError):
            self.store.cancel(two['id'])

    def test_duplicate_claim_under_concurrency(self):
        self.store.submit('Question',{})
        with ThreadPoolExecutor(max_workers=6) as pool:
            claims = list(pool.map(lambda i:self.store.claim(str(i),60,4),range(6)))
        self.assertEqual(sum(c is not None for c in claims),1)

    def test_quota_persist_restart_and_simulated_resumption(self):
        job = self.store.submit('Question',{})
        failures = [{'status':'api_error','error':{'category':'daily_quota_exhausted','message':'TPD exhausted','rate_limit':{'reset_at':time.time()+.04}}},
                    {'status':'ok','answer':'Grounded answer','sources':[]}]
        def pipeline(q,metadata):
            result = failures.pop(0)
            if result['status']=='ok':
                emit('groq_request',success=True,http_status=200,timestamp=time.time(),total_tokens=12,latency_ms=5)
            return result
        asyncio.run(Worker(self.store,self.config,pipeline).process_once())
        waiting = SQLiteQueue(self.path).get(job['id'])
        self.assertEqual(waiting['status'],'waiting_for_quota')
        self.assertFalse(waiting['reset_estimated'])
        self.assertFalse(asyncio.run(Worker(self.store,self.config,pipeline).process_once()))
        with patch('reliability.worker.time.time', return_value=time.time()+2):
            asyncio.run(Worker(SQLiteQueue(self.path),self.config,pipeline).process_once())
        self.assertEqual(self.store.get(job['id'])['status'],'completed')
        self.assertEqual(self.store.metrics()['api_successes'],1)

    def test_unknown_daily_reset_is_estimated(self):
        job = self.store.submit('Question',{})
        result = {'status':'api_error','error':{'category':'daily_quota_exhausted','message':'TPD'}}
        asyncio.run(Worker(self.store,self.config,lambda *_:result).process_once())
        self.assertEqual(self.store.get(job['id'])['status'],'waiting_for_quota')
        self.assertTrue(self.store.get(job['id'])['reset_estimated'])

    def test_permanent_errors_and_max_attempts(self):
        for category in ('authentication_error','client_error','server_error'):
            with self.subTest(category=category):
                job = self.store.submit(category,{})
                config = QueueConfig(database=self.path,max_attempts=1,inline_worker=False)
                asyncio.run(Worker(self.store,config,lambda *_:{'status':'api_error','error':{'category':category,'message':'Failure'}}).process_once())
                self.assertEqual(self.store.get(job['id'])['status'],'failed')

    def test_retry_schedule_and_public_verdict(self):
        job = self.store.submit('Question',{})
        asyncio.run(Worker(self.store,self.config,lambda *_:{'status':'api_error','error':{'category':'server_error','message':'temporary'}}).process_once())
        self.assertEqual(self.store.get(job['id'])['status'],'retry_scheduled')
        time.sleep(.03)
        asyncio.run(Worker(self.store,self.config,lambda *_:{'status':'ok','answer':'Answer','verification':{'raw_response':'hidden','verified':True}}).process_once())
        self.assertNotIn('raw_response',self.store.get(job['id'])['result']['verification'])

    def test_api_endpoints_and_auth(self):
        app = create_app(self.config, lambda *_:{'status':'ok','answer':'Answer'})
        with patch.dict(os.environ,{'RELAY_ADMIN_TOKEN':'operator'}), TestClient(app) as client:
            self.assertEqual(client.get('/jobs').status_code,401)
            client.headers['Authorization']='Bearer operator'
            result = client.post('/ask',json={'question':'Question'},headers={'Idempotency-Key':'one'})
            self.assertEqual(result.status_code,202)
            job_id=result.json()['job_id']
            self.assertEqual(client.post('/ask',json={'question':'Question'},headers={'Idempotency-Key':'one'}).json()['job_id'],job_id)
            self.assertEqual(client.get('/jobs').json()['total'],1)
            self.assertEqual(client.get(f'/jobs/{job_id}/events').json()['items'][0]['stage'],'request_received')
            self.assertEqual(client.get('/queue/metrics').status_code,200)
            self.assertEqual(client.get('/health').status_code,200)
            self.assertEqual(client.post(f'/jobs/{job_id}/cancel').json()['status'],'cancelled')
            self.assertEqual(client.post(f'/jobs/{job_id}/cancel').status_code,409)
            self.assertEqual(client.get('/jobs/missing').status_code,404)
            self.assertEqual(client.post('/ask',json={'question':'   '}).status_code,400)

    def test_application_lifespan_worker_and_sync_compatibility(self):
        config = QueueConfig(database=self.path,poll_seconds=.01)
        def pipeline(*_):
            emit('query_analysis',valid=True)
            return {'status':'ok','answer':'Answer','sources':[{'paper':'paper.pdf','page':1}]}
        with patch.dict(os.environ,{'RELAY_ADMIN_TOKEN':'operator'}), TestClient(create_app(config,pipeline)) as client:
            client.headers['Authorization']='Bearer operator'
            response=client.post('/ask',json={'question':'Question','wait_seconds':2})
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json()['answer'],'Answer')
            events=client.get('/jobs/'+response.json()['job_id']+'/events').json()['items']
            self.assertTrue(any(e['stage']=='query_analysis' for e in events))

    def test_sanitizing_errors(self):
        self.assertNotIn('gsk_secret',sanitize('api_key=gsk_secret'))
        self.assertEqual(public_result('A mask-based attention method.'),'A mask-based attention method.')
        self.assertEqual(public_result('mask-based ' * 200),'mask-based ' * 200)


class LoadTests(unittest.TestCase):
    def config(self,**kwargs):
        values=dict(provider='groq',scenario='baseline',max_requests=3,concurrency=1,max_tokens=3000,max_runtime_seconds=1,dry_run=False,confirmed=True)
        values.update(kwargs)
        return LoadConfig(**values)

    def run_mock(self,config,handler):
        with patch.dict(os.environ,{'GROQ_LOAD_TEST_ENABLED':'true','GROQ_API_KEY':'test-key'}):
            runner=LoadRunner(config,transport=httpx.MockTransport(handler))
            asyncio.run(runner.run())
            return runner

    def test_explicit_optin(self):
        with patch.dict(os.environ,{'GROQ_LOAD_TEST_ENABLED':'false'}):
            with self.assertRaises(ValueError):
                self.config().validate()
        with self.assertRaises(ValueError):
            self.config(concurrency=100).validate()

    def test_dry_run_never_calls(self):
        runner=LoadRunner(self.config(dry_run=True),transport=httpx.MockTransport(lambda _:self.fail('Network attempted')))
        self.assertEqual(asyncio.run(runner.run()),[])

    def test_request_limit_tokens_csv_and_missing_ttft(self):
        runner=self.run_mock(self.config(),lambda _:httpx.Response(200,json={'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15}}))
        self.assertEqual(len(runner.rows),3)
        self.assertTrue(all(r['ttft_ms'] is None for r in runner.rows))
        with tempfile.TemporaryDirectory() as temp:
            results,summary=Path(temp)/'results.csv',Path(temp)/'summary.csv'
            write_csv(runner.rows,results,summary)
            with results.open() as f:
                rows=list(csv.DictReader(f))
            self.assertEqual(rows[0]['provider'],'groq')
            with summary.open() as f:
                self.assertEqual(list(csv.DictReader(f))[0]['total_tokens_consumed'],'45')
        runner=self.run_mock(self.config(max_tokens=600),lambda _:httpx.Response(200,json={}))
        self.assertLessEqual(runner.reserved_tokens,600)
        self.assertEqual(len(runner.rows),1)

    def test_daily_quota_and_temporary_rate_limit_stop(self):
        for message in ('Tokens per day (TPD) limit reached.','Too many requests'):
            runner=self.run_mock(self.config(),lambda _:httpx.Response(429,json={'error':{'message':message}}))
            self.assertEqual(len(runner.rows),1)
            self.assertFalse(runner.rows[0]['success'])

    def test_recovery_stays_in_budget(self):
        calls=[]
        def handler(_):
            calls.append(1)
            if len(calls)==1:
                return httpx.Response(429,headers={'retry-after':'0'},json={'error':{'message':'temporary'}})
            return httpx.Response(200,json={'usage':{'total_tokens':10}})
        runner=self.run_mock(self.config(scenario='recovery',max_requests=2),handler)
        self.assertEqual(len(runner.rows),2)
        self.assertTrue(runner.rows[1]['completed_after_retry'])

    def test_runtime_and_stop(self):
        async def slow(_):
            await asyncio.sleep(.1)
            return httpx.Response(200,json={})
        runner=self.run_mock(self.config(max_runtime_seconds=.02),slow)
        self.assertEqual(len(runner.rows),1)
        self.assertEqual(runner.rows[0]['error_category'],'timeout')
        async def stopped():
            with patch.dict(os.environ,{'GROQ_LOAD_TEST_ENABLED':'true','GROQ_API_KEY':'test'}):
                instance=LoadRunner(self.config(),transport=httpx.MockTransport(lambda _:httpx.Response(200,json={})))
                instance.stop()
                await instance.run()
                return instance.rows
        self.assertEqual(asyncio.run(stopped()),[])


if __name__=='__main__':
    unittest.main()
