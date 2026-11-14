"""Experiment access and provider isolation without live traffic."""
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient
from reliability.api import create_app
from reliability.config import QueueConfig
from reliability.experiments import ExperimentManager
from reliability.load_testing import LoadConfig
from reliability.store import SQLiteQueue


class ExperimentAPITests(unittest.TestCase):
    def test_dryrun_safety_and_csv(self):
        with tempfile.TemporaryDirectory() as temp:
            app=create_app(QueueConfig(database=str(Path(temp)/'db'),inline_worker=False))
            with patch.dict(os.environ,{'RELAY_ADMIN_TOKEN':'operator','GROQ_LOAD_TEST_ENABLED':'false'}),TestClient(app) as client:
                client.headers['Authorization']='Bearer operator'
                body={'provider':'groq','scenario':'baseline','max_requests':2,'concurrency':1,'max_tokens':1000,'max_runtime_seconds':2,'dry_run':True}
                result=client.post('/experiments/run',json=body)
                self.assertEqual(result.status_code,200)
                self.assertEqual(result.json()['status'],'dry_run')
                run_id=result.json()['id']
                self.assertIn('provider,request_id,scenario',client.get(f'/experiments/{run_id}/csv').text)
                self.assertEqual(client.get('/experiments/results').json()['runs'][0]['id'],run_id)
                body.update(dry_run=False,confirmed=True)
                self.assertEqual(client.post('/experiments/run',json=body).status_code,400)
                with patch.dict(os.environ,{'GROQ_LOAD_TEST_ENABLED':'true'}):
                    self.assertEqual(client.post('/experiments/run',json={'provider':'groq','dry_run':False,'confirmed':True}).status_code,400)
                body.update(dry_run=True,concurrency=100)
                self.assertEqual(client.post('/experiments/run',json=body).status_code,400)

    def test_running_real_experiment_blocks_new_job_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            store=SQLiteQueue(str(Path(temp)/'db'))
            ExperimentManager(store)
            with store.connection(True) as db:
                db.execute("INSERT INTO experiments(id,status,config,deadline) VALUES('run','running',?,?)",('{"provider":"groq"}',time.time()+60))
            store.submit('Queued question',{})
            self.assertIsNone(store.claim('worker',120,1))
            with store.connection(True) as db:
                db.execute("UPDATE experiments SET status='completed'")
            self.assertIsNotNone(store.claim('worker',120,1))

    def test_no_token_access_is_loopback_only(self):
        with tempfile.TemporaryDirectory() as temp:
            app=create_app(QueueConfig(database=str(Path(temp)/'db'),inline_worker=False))
            with patch.dict(os.environ,{'RELAY_ADMIN_TOKEN':'','RELAY_LOCAL_MODE':'true'}):
                with TestClient(app,client=('203.0.113.1',4000)) as external:
                    self.assertEqual(external.get('/queue/metrics').status_code,503)
                with TestClient(app,client=('127.0.0.1',4000)) as local:
                    self.assertEqual(local.get('/queue/metrics').status_code,200)


if __name__=='__main__':
    unittest.main()
