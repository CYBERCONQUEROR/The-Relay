import pandas as pd

df = pd.read_csv("results/results1.csv")

successful = df[df["status"] == "ok"].copy()

successful["total_tokens"] = (
    successful["tokens_in"] + successful["tokens_out"]
)

summary = successful.groupby("scenario").agg(
    successful_answers=("request_id", "count"),
    avg_input_tokens=("tokens_in", "mean"),
    avg_output_tokens=("tokens_out", "mean"),
    avg_tokens_per_answer=("total_tokens", "mean")
).round(2)
summary["cost_per_1000_usd"] = (
    1000 * (
        summary["avg_input_tokens"] * 0.15
        + summary["avg_output_tokens"] * 0.60
    ) / 1_000_000
)

print(summary)
