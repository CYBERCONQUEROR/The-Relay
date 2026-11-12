
import csv
import time
import pandas as pd

from retrieval.retriever import PaperRetriever


DATASET = "experiments/retrieval_eval.csv"
OUTPUT = "results/retrieval_evaluation.csv"

retriever = PaperRetriever()

settings = ["R1", "R2", "R3"]

records = []


with open(DATASET, "r", encoding="utf-8") as file:
    reader = csv.DictReader(file)

    for row in reader:

        query = row["query"]
        expected_paper = row["expected_paper"].strip()

        expected_pages = {
            page.strip()
            for page in row["expected_pages"].split(";")
            if page.strip()
        }

        if not expected_pages or "REPLACE" in expected_pages:
            raise ValueError(
                f"Add verified expected pages for: {query}"
            )

        for setting in settings:

            start = time.perf_counter()

            results = retriever.search(
                query,
                top_k=5,
                setting=setting
            )

            retrieval_ms = (
                time.perf_counter() - start
            ) * 1000

            ranks = []

            for rank, result in enumerate(results, start=1):

                actual_paper = result["paper"]
                actual_page = str(result["page"])

                if (
                    actual_paper == expected_paper
                    and actual_page in expected_pages
                ):
                    ranks.append(rank)

            hit_at_1 = int(1 in ranks)
            hit_at_3 = int(any(r <= 3 for r in ranks))
            hit_at_5 = int(len(ranks) > 0)

            reciprocal_rank = (
                1 / min(ranks) if ranks else 0
            )

            records.append({
                "query": query,
                "setting": setting,
                "expected_paper": expected_paper,
                "hit_at_1": hit_at_1,
                "hit_at_3": hit_at_3,
                "hit_at_5": hit_at_5,
                "reciprocal_rank": reciprocal_rank,
                "retrieval_ms": round(retrieval_ms, 2)
            })


df = pd.DataFrame(records)

df.to_csv(OUTPUT, index=False)

summary = (
    df.groupby("setting")
    .agg(
        queries=("query", "count"),
        hit_at_1=("hit_at_1", "mean"),
        hit_at_3=("hit_at_3", "mean"),
        hit_at_5=("hit_at_5", "mean"),
        MRR=("reciprocal_rank", "mean"),
        avg_retrieval_ms=("retrieval_ms", "mean")
    )
    .reset_index()
)

for column in ["hit_at_1", "hit_at_3", "hit_at_5", "MRR"]:
    summary[column] = (summary[column] * 100).round(2)

summary["avg_retrieval_ms"] = (
    summary["avg_retrieval_ms"].round(2)
)

print("\n========== RETRIEVAL EVALUATION ==========\n")
print(summary.to_string(index=False))

summary.to_csv(
    "results/retrieval_summary.csv",
    index=False
)

print("\nSaved:", OUTPUT)
print("Saved: results/retrieval_summary.csv")
