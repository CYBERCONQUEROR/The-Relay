"""Explicit offline UI QA fixture. Never used by main.py or production workers.

Run: python test/serve_mock_dashboard.py --port 8010
Uses the existing orchestrator with mocked retrieval and mocked Groq HTTP only.
A fresh temporary queue isolates all QA jobs from the real application.
"""
import argparse
import json
from pathlib import Path
import sys
import tempfile
import threading
from unittest.mock import Mock, patch

sys.path.insert(0,str(Path(__file__).resolve().parent.parent))
import httpx
from groq import Groq
import uvicorn
from pipeline import groq_client, orchestrator
from reliability.api import create_app
from reliability.config import QueueConfig


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--port',type=int,default=8010)
    args=parser.parse_args()
    lock=threading.Lock()
    calls=0
    def handler(request):
        nonlocal calls
        with lock:
            calls+=1
            current=calls
        if current==1:
            return httpx.Response(429,headers={'retry-after':'4'},json={'error':{'message':'OFFLINE TEST: Tokens per day (TPD) exhausted'}})
        messages=json.loads(request.content)['messages']
        is_verifier='strict research evidence verifier' in messages[0]['content']
        answer=json.dumps({'supported':True,'reason':'Offline fixture evidence matches','unsupported_claims':[]}) if is_verifier else 'OFFLINE TEST: Multi-head attention combines parallel attention projections [attention_is_all_you_need_v7.pdf, p. 5].'
        return httpx.Response(200,headers={'x-ratelimit-remaining-tokens':'1000'},json={
            'id':'offline-fixture','object':'chat.completion','created':1,'model':'mock-groq-offline',
            'choices':[{'index':0,'finish_reason':'stop','message':{'role':'assistant','content':answer}}],
            'usage':{'prompt_tokens':100,'completion_tokens':50,'total_tokens':150}})
    retriever=Mock()
    retriever.search.return_value=[{'paper':'attention_is_all_you_need_v7.pdf','page':5,'text':'OFFLINE FIXTURE: Multi-head attention uses parallel learned projections.','score':.8}]
    client=Groq(api_key='offline-test',max_retries=0,http_client=httpx.Client(transport=httpx.MockTransport(handler)))
    with tempfile.TemporaryDirectory(prefix='relay-ui-qa-') as temp,client,patch.object(groq_client,'client',client),patch.object(groq_client,'API_KEY','offline-test'),patch.object(orchestrator,'get_retriever',return_value=retriever):
        app=create_app(QueueConfig(database=str(Path(temp)/'jobs.sqlite3'),poll_seconds=.1))
        print('OFFLINE UI QA — mocked Groq HTTP and retrieval; no real API calls.',flush=True)
        uvicorn.run(app,host='127.0.0.1',port=args.port,log_level='warning')


if __name__=='__main__':
    main()
