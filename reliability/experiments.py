"""Persistent experiment reports with bounded execution and explicit opt-in."""
import asyncio
import csv
import io
import json
from pathlib import Path
import time
import uuid

from fastapi import Depends, HTTPException, Query
from fastapi.responses import Response
from pydantic import BaseModel, Field

from reliability.load_testing import FIELDS, LoadConfig, LoadRunner, summarize, write_csv
from reliability.telemetry import sanitize


class ExperimentRequest(BaseModel):
    provider: str = 'standin'
    scenario: str = 'baseline'
    max_requests: int = Field(5, ge=1, le=1000)
    concurrency: int = Field(1, ge=1, le=100)
    max_tokens: int = Field(3000, ge=1, le=1000000)
    max_runtime_seconds: float = Field(30, gt=0, le=600)
    output_tokens: int = Field(64, ge=1, le=256)
    dry_run: bool = True
    confirmed: bool = False


class ExperimentManager:
    def __init__(self, store):
        self.store = store
        self.tasks = {}
        with store.connection(True) as db:
            db.execute('CREATE TABLE IF NOT EXISTS experiments(id TEXT PRIMARY KEY,created REAL,status TEXT,config TEXT,rows TEXT,summary TEXT,stop INTEGER DEFAULT 0,deadline REAL,error TEXT)')

    def _expire(self, db):
        db.execute("UPDATE experiments SET status='interrupted',error='Execution interrupted; tests never resume automatically' WHERE status='running' AND deadline<?", (time.time(),))

    def list(self):
        with self.store.connection(True) as db:
            self._expire(db)
            rows = db.execute('SELECT * FROM experiments ORDER BY created DESC LIMIT 100').fetchall()
            return [self.decode(r) for r in rows]

    @staticmethod
    def decode(row):
        if not row:
            return None
        obj = dict(row)
        for key in ('config','rows','summary'):
            obj[key] = json.loads(obj[key]) if obj[key] else None
        return obj

    def get(self, run_id):
        with self.store.connection(True) as db:
            self._expire(db)
            return self.decode(db.execute('SELECT * FROM experiments WHERE id=?',(run_id,)).fetchone())

    def start(self, config):
        config.validate()
        run_id = str(uuid.uuid4())
        with self.store.connection(True) as db:
            self._expire(db)
            if db.execute("SELECT 1 FROM experiments WHERE status='running'").fetchone():
                raise ValueError('An experiment is already running. Stop it or wait for completion.')
            if config.provider == 'groq' and db.execute("SELECT 1 FROM jobs WHERE status IN ('pending','processing','waiting_for_quota','retry_scheduled')").fetchone():
                raise ValueError('Drain or cancel the real-provider queue before a Groq experiment')
            if config.provider == 'groq' and not config.dry_run:
                gate = db.execute("SELECT value FROM controls WHERE key='provider_not_before'").fetchone()
                if gate and float(gate[0]) > time.time():
                    raise ValueError('The real provider is in a recorded quota cooldown; no live test admitted')
            db.execute('INSERT INTO experiments(id,created,status,config,rows,summary,deadline) VALUES(?,?,?,?,?,?,?)',
                       (run_id,time.time(),'dry_run' if config.dry_run else 'running',json.dumps(config.summary()),'[]','{}',time.time()+config.max_runtime_seconds+5))
        if not config.dry_run:
            self.tasks[run_id] = asyncio.create_task(self.execute(run_id,config))
        return self.get(run_id)

    async def execute(self, run_id, config):
        def should_stop():
            with self.store.connection() as db:
                return bool(db.execute('SELECT stop FROM experiments WHERE id=?',(run_id,)).fetchone()[0])
        rows = []
        def on_row(row):
            rows.append(row)
            with self.store.connection(True) as db:
                db.execute('UPDATE experiments SET rows=?,summary=? WHERE id=?',(json.dumps(rows),json.dumps(summarize(rows)),run_id))
        runner = LoadRunner(config,on_row=on_row,should_stop=should_stop)
        try:
            await runner.run()
            # Never overwrite the user's pre-existing experiment CSVs.
            folder = Path('results') / ('groq' if config.provider == 'groq' else 'standin') / run_id
            write_csv(rows,folder/'results.csv',folder/'summary.csv')
            with self.store.connection(True) as db:
                db.execute('UPDATE experiments SET status=?,summary=?,error=? WHERE id=?',
                           ('stopped' if should_stop() else 'completed',json.dumps(summarize(rows)),runner.stop_reason,run_id))
        except Exception as error:
            with self.store.connection(True) as db:
                db.execute("UPDATE experiments SET status='failed',error=? WHERE id=?",(sanitize(error),run_id))
        finally:
            self.tasks.pop(run_id,None)

    def stop(self, run_id):
        with self.store.connection(True) as db:
            db.execute('UPDATE experiments SET stop=1 WHERE id=?',(run_id,))
        return self.get(run_id)

    async def shutdown(self):
        for run_id in list(self.tasks):
            self.stop(run_id)
        if self.tasks:
            await asyncio.gather(*list(self.tasks.values()), return_exceptions=True)


def install_routes(app, access):
    @app.post('/experiments/run', dependencies=[Depends(access)])
    async def run(body: ExperimentRequest):
        try:
            if body.provider == 'groq' and not body.dry_run and not {'max_requests','max_tokens','max_runtime_seconds'} <= body.model_fields_set:
                raise ValueError('Live Groq tests require explicit request, token and runtime budgets')
            return app.state.experiments.start(LoadConfig(**body.model_dump()))
        except ValueError as error:
            raise HTTPException(400,str(error))

    @app.get('/experiments/results', dependencies=[Depends(access)])
    def results():
        saved = []
        for path in sorted(Path('results').glob('*.csv')):
            name = path.name
            provider = 'groq' if name.startswith('groq_load') else 'standin' if name in ('results.csv','results1.csv','load_test_results_final.csv','peak_queue_summary.csv') else 'evaluation'
            saved.append({'filename':name,'provider':provider,'url':'/experiments/files/'+name})
        return {'runs':app.state.experiments.list(),'files':saved}

    @app.get('/experiments/files/{filename}', dependencies=[Depends(access)])
    def file(filename: str):
        if Path(filename).name != filename or not filename.endswith('.csv'):
            raise HTTPException(400,'Invalid CSV filename')
        path = Path('results')/filename
        if not path.is_file():
            raise HTTPException(404,'CSV not found')
        from fastapi.responses import FileResponse
        return FileResponse(path,media_type='text/csv',filename=filename)

    @app.get('/experiments/{run_id}', dependencies=[Depends(access)])
    def details(run_id: str):
        result = app.state.experiments.get(run_id)
        if not result:
            raise HTTPException(404,'Experiment not found')
        return result

    @app.post('/experiments/{run_id}/stop', dependencies=[Depends(access)])
    def stop(run_id: str):
        if not app.state.experiments.get(run_id):
            raise HTTPException(404,'Experiment not found')
        return app.state.experiments.stop(run_id)

    @app.get('/experiments/{run_id}/csv', dependencies=[Depends(access)])
    def download(run_id: str, summary: bool = Query(False)):
        run = app.state.experiments.get(run_id)
        if not run:
            raise HTTPException(404,'Experiment not found')
        buffer = io.StringIO()
        rows = [run['summary']] if summary else run['rows']
        fields = list(run['summary']) if summary else FIELDS
        writer = csv.DictWriter(buffer,fieldnames=fields)
        writer.writeheader()
        for row in rows:
            writer.writerow({k:json.dumps(v) if isinstance(v,dict) else v for k,v in row.items()})
        return Response(buffer.getvalue(),media_type='text/csv',headers={'Content-Disposition':f'attachment; filename="{run["config"]["provider"]}_{run_id}.csv"'})
