
import pandas as pd
import numpy as np

df = pd.read_csv("results/results.csv")

summary = []

for scenario, group in df.groupby("scenario"):

    all_latency = group["total_ms"].dropna()

    successful_group = group[group["status"] == "ok"]
    successful_latency = successful_group["total_ms"].dropna()

    def percentile(values, p):
        if len(values) == 0:
            return None
        return round(float(np.percentile(values, p)), 2)

    total = len(group)
    successful = len(successful_group)

    summary.append({
        "scenario": scenario,
        "total_requests": total,
        "successful": successful,
        "rate_limited": (group["status"] == "rate_limited").sum(),
        "timeouts": (group["status"] == "timeout").sum(),
        "errors": (group["status"] == "error").sum(),
        "success_rate_percent": round(successful / total * 100, 2),

        "all_attempts_p50_ms": percentile(all_latency, 50),
        "all_attempts_p95_ms": percentile(all_latency, 95),

        "success_p50_ms": percentile(successful_latency, 50),
        "success_p95_ms": percentile(successful_latency, 95),
        "success_p99_ms": percentile(successful_latency, 99),

        "mean_success_ms": (
            round(successful_latency.mean(), 2)
            if len(successful_latency) else None
        ),

        "successful_tokens_in": successful_group["tokens_in"].sum(),
        "successful_tokens_out": successful_group["tokens_out"].sum()
    })


result = pd.DataFrame(summary)

print(result.to_string(index=False))

result.to_csv(
    "results/experiment_summary.csv",
    index=False
)
