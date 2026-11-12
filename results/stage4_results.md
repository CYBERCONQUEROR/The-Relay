# Stage 4 — Automated Load Experiments

## Objective

An automated Python experiment runner was developed to evaluate the stand-in service under different traffic patterns and quota configurations. Request-level data was saved to `results/results.csv`, and aggregated performance metrics were generated in `results/experiment_summary.csv`.

## Observed results

| Scenario | Total | Successful | Rate limited | Success rate |
|---|---:|---:|---:|---:|
| Steady | 20 | 20 | 0 | 100% |
| Burst | 30 | 30 | 0 | 100% |
| Concurrent overload | 100 | 54 | 46 | 54% |
| Halved RPM | 510 | 500 | 10 | 98.04% |
| Daily budget exhaustion | 15 | 9 | 6 | 60% |

## Latency observations

The steady experiment had a mean latency of 307.76 ms. Burst traffic increased the mean latency to 979.57 ms. Concurrent overload produced a mean latency of 349.44 ms, with a p95 of 1,012.52 ms and a p99 of 1,373.98 ms.

The halved-RPM experiment accepted 500 of 510 requests. The daily-budget experiment accepted nine of 15 requests.

## Interpretation

The experiments demonstrate that the stand-in service can enforce request and token quotas. Burst traffic increased response latency, while the concurrent-overload and quota-exhaustion scenarios resulted in rejected requests.

These results are measurements from the local simulator, not measurements of real Groq infrastructure. The concurrent-overload scenario should not be interpreted as a sustained-duration test.

## Output files

- `results/results.csv`
- `results/experiment_summary.csv`

The raw CSV contains 675 request records from the latest run.