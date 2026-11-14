"""Production entry-point smoke checks with an empty, temporary database."""
import importlib
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

from fastapi.testclient import TestClient


class StartupTests(unittest.TestCase):
    def test_main_application_starts_without_provider_calls(self):
        with tempfile.TemporaryDirectory() as temp,patch.dict(os.environ,{
            'QUEUE_DATABASE':str(Path(temp)/'main.db'),'QUEUE_INLINE_WORKER':'true',
            'GROQ_API_KEY':'','RELAY_ADMIN_TOKEN':'operator',
        }):
            module=importlib.import_module('main')
            with TestClient(module.app) as client:
                client.headers['Authorization']='Bearer operator'
                self.assertEqual(client.get('/').status_code,200)
                self.assertEqual(client.get('/health').status_code,200)
                self.assertEqual(client.get('/queue/metrics').json()['api_requests'],0)

    def test_standalone_worker_starts_and_stops(self):
        with tempfile.TemporaryDirectory() as temp:
            env={**os.environ,'QUEUE_DATABASE':str(Path(temp)/'worker.db'),'GROQ_API_KEY':''}
            process=subprocess.Popen([sys.executable,'-m','reliability.worker'],stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,env=env)
            try:
                time.sleep(1)
                self.assertIsNone(process.poll(),'Worker exited unexpectedly')
                process.terminate()
                stdout,stderr=process.communicate(timeout=10)
                self.assertEqual(process.returncode,0,stderr)
                self.assertIn('worker_started',stderr+stdout)
            finally:
                if process.poll() is None:
                    process.kill()
                    process.communicate()


if __name__=='__main__':
    unittest.main()
