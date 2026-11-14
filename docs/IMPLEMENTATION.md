# Implementation and validation record

This upgrade was implemented inside the existing repository. No live Groq calls or live load tests were run. Existing results CSV changes predated the upgrade and were left untouched.

## New source files

| Files | Responsibility |
| --- | --- |
| `reliability/__init__.py`, `config.py` | Queue package and validated environment configuration |
| `reliability/store.py` | SQLite schema, atomic admission/claim, bounded active capacity, fencing, events, cancellation, metrics |
| `reliability/worker.py` | Bounded worker loops, lease heartbeat, retry scheduling, crash recovery, result persistence, standalone entry point |
| `reliability/telemetry.py` | Context-local job/model tracing, safe logs, redaction |
| `reliability/api.py` | FastAPI job, lifecycle SSE/polling, health and monitoring routes; operator access and CORS; frontend serving |
| `reliability/load_testing.py`, `experiments.py` | Controlled direct-provider experiments, shared admission, caps/stop, durable reports, downloadable CSVs |
| `experiments/run_groq_load_tests.py` | Explicit-budget dry-run/live CLI (live requires flags and environment opt-in) |
| `frontend/package.json`, `package-lock.json`, `tsconfig.json`, `vite.config.ts`, `index.html` | React/Vite/TypeScript build and test setup with locked npm dependencies |
| `frontend/src/main.tsx`, `App.tsx`, `api.ts`, `style.css` | Functional responsive dashboard, polling, authenticated API/downloads, six operation pages |
| `frontend/tests/setup.ts`, `App.test.tsx`, `browser-smoke.cjs` | Mocked frontend tests and optional browser QA |
| `test/test_queue.py` | Queue, API, lease/concurrency/capacity, retry/resumption, budgets and CSV tests |
| `test/test_pipeline_regression.py` | Original RAG decisions, citations and correction behavior with mocks |
| `test/test_groq_transport.py` | Real SDK raw-response integration over mocked HTTP; full pipeline quota wait/resumption |
| `test/test_experiment_api.py` | Experiment safety, provider/worker interlock, access restrictions and reports |
| `test/test_startup.py` | Production API import/lifespan and separate worker startup/shutdown, without provider calls |
| `test/serve_mock_dashboard.py` | Explicit offline browser QA fixture with temporary SQLite, mocked retrieval and mocked Groq HTTP |
| `docs/RELIABILITY.md`, `docs/IMPLEMENTATION.md`, `docs/screenshots/*.png` | Operator guide, this report, labeled browser QA captures |

## Modified files

- `main.py`: delegates app construction to the queue API with lifecycle-managed workers.
- `pipeline/orchestrator.py`: preserves synchronous evaluation entry point, supports a per-job settings snapshot, lazy-loads retrieval, and emits operational stage events.
- `pipeline/groq_client.py`: retains bounded retry policy, captures actual response headers through the SDK raw-response path, parses supported reset hints, sanitizes errors, emits per-attempt telemetry, and honors the submitted model snapshot.
- `retrieval/retriever.py`: keeps hybrid RRF ordering but supplies comparable cosine confidence for BM25-only hits. Previously BM25 values could incorrectly pass the semantic confidence threshold. R4 defaults and citation workflow remain unchanged.
- `test/test_groq_retry.py`: adapts existing mocked responses to the SDK raw-response wrapper while retaining the five original retry assertions.
- `.env.example`: queue, access, CORS, stand-in and disabled-by-default load-test configuration.
- `.gitignore`: ignores runtime databases, frontend dependencies/build output and local test output.
- `README.md`: v2 startup, API migration notes, guide/report links and corrected streaming/health descriptions; historical results retained.

## Validation

- Backend: **38 tests passed**, including all five original mocked retry tests.
- RAG regression: success and cited-only sources; low-confidence refusal without a provider call; one verifier correction; verification failure; API failure distinct from refusal; citation/page checks.
- Actual Groq SDK against mocked HTTP: successful raw headers/usage, 403, 500/502/503/504, persisted daily exhaustion, scheduled resumption, verified final answer and confirmation-only recovery event.
- Frontend: **13 mocked API tests passed**. Covers submission, answer/citations/refusal, temporary/daily limits, resumed/failed jobs, reconnect recovery, queue filtering/refresh, simple view, live-test approval and source distinction.
- TypeScript and Vite production build passed.
- Load-test CLI dry-run passed with 3-request / 2,000 estimated-token / 30-second limits; **zero provider calls**.
- API lifespan/worker startup is exercised by integration tests. Worker processing and crash/restart behavior are exercised against temporary SQLite databases.

The root manual scripts `test_groq.py`, `test_pipeline.py` and `evaluate_answers.py` use live quota; they were not run. Offline regression tests exercise their shared pipeline instead. Retrieval benchmark scripts were not rerun against the entire corpus and no benchmark CSVs were replaced. `git diff --check` is clean for changed source/configuration; pre-existing results CSVs have CRLF-related whitespace differences and were excluded from that source check.

Run all offline checks:

```bash
python -m unittest discover -s test -v
npm --prefix frontend test
npm --prefix frontend run build
```

For an isolated browser workflow without spending Groq quota:

```bash
npm --prefix frontend run build
python test/serve_mock_dashboard.py --port 8010
```

Open http://127.0.0.1:8010 and ask a question. The fixture's first mocked Groq call produces a daily limit; its provider retry hint schedules a retry after 4 seconds. The existing orchestrator then completes generation and verification through mocked HTTP. Answers visibly say **OFFLINE TEST**. The fixture owns a temporary database and never substitutes itself into `main.py`. This is a scheduler/UI verification fixture, not evidence of real provider recovery.

## Operation and limitations

Architecture diagram, schema, endpoint contracts and JSON examples, environment variables, separate worker commands, CSV locations, live-test approval steps and known limitations are in [RELIABILITY.md](RELIABILITY.md).

The principal limitations are single-operator authentication, single-host SQLite, retained-history growth, estimated token budgets, last-observed provider health, and at-least-once provider calls after uncertain outcomes. A successful simulated reset confirms queue behavior only. Real Groq recovery remains unverified until the operator explicitly approves a live test or submits a real research question.

## Browser verification and screenshots

Headless Chromium verified desktop (1512×982) and mobile (390×844), the completed answer/citations, queue navigation and Groq approval gate. No JavaScript page errors or mobile horizontal overflow were observed. All screenshots use the isolated offline fixture and carry a visible **OFFLINE QA · MOCKED GROQ RESPONSES** label.

- [Research workspace](screenshots/workspace-desktop.png)
- [Quota wait](screenshots/quota-wait-desktop.png)
- [Completed request and lifecycle](screenshots/completed-request-desktop.png)
- [Persistent queue](screenshots/queue-desktop.png)
- [Monitoring](screenshots/monitoring-desktop.png)
- [Live-test confirmation](screenshots/load-test-confirmation-desktop.png)
- [Mobile workspace](screenshots/workspace-mobile.png)

Optional browser smoke script (requires an available Playwright Node module and Chromium, in addition to the offline QA server):

```bash
# Set these paths to your installed Playwright module and Chromium executable:
RELAY_PLAYWRIGHT_MODULE=/absolute/path/to/playwright \
RELAY_CHROMIUM_PATH=/usr/bin/chromium \
node frontend/tests/browser-smoke.cjs
```

The script assumes a freshly started fixture so its first mocked call enters quota waiting. It writes screenshots under `docs/screenshots/` and never approves a live test.
