"""Validate the real SDK raw-response path against a mocked HTTP transport."""
import asyncio
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import Mock, patch

import httpx
from groq import Groq
from pipeline import groq_client, orchestrator
from reliability.config import QueueConfig
from reliability.store import SQLiteQueue
from reliability.telemetry import trace
from reliability.worker import Worker


def completion(answer):
    return {'id':'mock-id','object':'chat.completion','created':1,'model':'mock-model',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':answer}}],
            'usage':{'prompt_tokens':10,'completion_tokens':5,'total_tokens':15,'total_time':.01,'queue_time':.002}}


class GroqTransportTests(unittest.TestCase):
    def test_success_raw_headers_and_metadata(self):
        events=[]
        def handle(request):
            self.assertEqual(request.headers['authorization'],'Bearer offline-key')
            return httpx.Response(200,headers={'x-ratelimit-remaining-tokens':'500'},json=completion('Answer'))
        client=Groq(api_key='offline-key',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handle)))
        with client,patch.object(groq_client,'client',client),patch.object(groq_client,'API_KEY','offline-key'),trace(events.append):
            result=groq_client.generate_completion([{'role':'user','content':'Question'}])
        self.assertEqual(result['content'],'Answer')
        self.assertEqual(result['rate_limit']['x-ratelimit-remaining-tokens'],'500')
        self.assertEqual(events[-1]['data']['total_tokens'],15)
        self.assertIsNone(events[-1]['data']['ttft_ms'])
        self.assertEqual(events[-1]['data']['api_processing_ms'],10)
        self.assertEqual(events[-1]['data']['queue_wait_ms'],2)

    def test_403_and_server_error_codes(self):
        for status in (403,500,502,503,504):
            with self.subTest(status=status):
                count=[]
                def handler(_):
                    count.append(1)
                    return httpx.Response(status,json={'error':{'message':'Test failure'}})
                client=Groq(api_key='offline-key',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler)))
                with client,patch.object(groq_client,'client',client),patch.object(groq_client,'API_KEY','offline-key'),patch.object(groq_client.time,'sleep'),self.assertRaises(groq_client.GroqRequestError) as raised:
                    groq_client.generate_completion([{'role':'user','content':'Question'}])
                self.assertEqual(len(count),1 if status==403 else groq_client.MAX_ATTEMPTS)
                self.assertEqual(raised.exception.category,'authentication_error' if status==403 else 'server_error')

    def test_full_pipeline_quota_wait_and_resumption(self):
        requests=[]
        answer='Supported answer [paper.pdf, p. 3].'
        def handler(request):
            requests.append(request)
            if len(requests)==1:
                return httpx.Response(429,headers={'retry-after':'1'},json={'error':{'message':'Tokens per day (TPD) exhausted'}})
            if len(requests)==2:
                return httpx.Response(200,json=completion(answer))
            return httpx.Response(200,json=completion('{"supported":true,"reason":"Supported by evidence","unsupported_claims":[]}'))
        retriever=Mock()
        retriever.search.return_value=[{'paper':'paper.pdf','page':3,'text':'Supported answer','score':.8}]
        client=Groq(api_key='offline-key',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler)))
        with tempfile.TemporaryDirectory() as temp,client,patch.object(groq_client,'client',client),patch.object(groq_client,'API_KEY','offline-key'),patch.object(orchestrator,'get_retriever',return_value=retriever):
            store=SQLiteQueue(str(Path(temp)/'jobs.db'))
            config=QueueConfig(database=store.path,inline_worker=False)
            job=store.submit('Question',{'setting':'R4','min_score':.25,'model':'mock-model'})
            worker=Worker(store,config)
            asyncio.run(worker.process_once())
            self.assertEqual(store.get(job['id'])['status'],'waiting_for_quota')
            with patch('reliability.worker.time.time',return_value=time.time()+2):
                asyncio.run(Worker(SQLiteQueue(store.path),config).process_once())
            result=store.get(job['id'])
            self.assertEqual(result['status'],'completed')
            self.assertEqual(result['result']['answer'],answer)
            stages={e['stage'] for e in store.events(job['id'])}
            self.assertTrue({'query_analysis','retrieval','evidence_check','generation','citation_verification','quota_recovery','completed'} <= stages)
            self.assertEqual(store.metrics()['api_requests'],3)
            self.assertEqual(store.metrics()['api_successes'],2)


if __name__=='__main__':
    unittest.main()
