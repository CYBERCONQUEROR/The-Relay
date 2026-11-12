import argparse
import csv
import sys
import time
from collections import defaultdict
from pathlib import Path

# Add project root to sys.path so the script can be run directly
sys.path.append(str(Path(__file__).resolve().parent.parent))

import numpy as np

from retrieval.retriever import PaperRetriever


# --------------------------------------------------
# 1. CLI arguments
# --------------------------------------------------

def parse_args():
    parser = argparse.ArgumentParser(
        description="Evaluate retrieval configurations."
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of results to retrieve per query (default: 5)"
    )
    parser.add_argument(
        "--settings",
        type=str,
        default="R1,R2,R3,R4,R5",
        help="Comma-separated retrieval settings to evaluate (default: R1,R2,R3,R4,R5)"
    )
    parser.add_argument(
        "--candidate-k",
        type=int,
        default=50,
        help="Candidate pool size for R2/R3/R4/R5 (default: 50)"
    )
    parser.add_argument(
        "--lambda-value",
        type=float,
        default=0.75,
        help="MMR lambda for R3 (default: 0.75)"
    )
    parser.add_argument(
        "--suffix",
        type=str,
        default="",
        help="Optional suffix appended to output CSV filenames (e.g. c300)"
    )
    return parser.parse_args()


args = parse_args()

SETTINGS = [s.strip() for s in args.settings.split(",")]
VALID_SETTINGS = {"R1", "R2", "R3", "R4", "R5"}
unknown_settings = set(SETTINGS) - VALID_SETTINGS
if unknown_settings:
    raise ValueError(f"Unknown settings: {sorted(unknown_settings)}. Choose from {sorted(VALID_SETTINGS)}.")
TOP_K = args.top_k
CANDIDATE_K = args.candidate_k
LAMBDA_VALUE = args.lambda_value

OUTPUT_DIR = Path("results")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)

suffix = f"_{args.suffix}" if args.suffix else ""
EVALUATION_FILE = OUTPUT_DIR / f"retrieval_evaluation{suffix}.csv"
SUMMARY_FILE    = OUTPUT_DIR / f"retrieval_summary{suffix}.csv"


# --------------------------------------------------
# 2. Evaluation questions
# --------------------------------------------------

questions = [

    # Attention Is All You Need
    {"id": "Q01", "query": "How does self-attention work in the Transformer?",                       "expected_paper": "attention_is_all_you_need_v7.pdf", "expected_pages": [3, 4, 5]},
    {"id": "Q02", "query": "What is the purpose of multi-head attention?",                           "expected_paper": "attention_is_all_you_need_v7.pdf", "expected_pages": [4, 5]},
    {"id": "Q03", "query": "Why does the Transformer use positional encoding?",                      "expected_paper": "attention_is_all_you_need_v7.pdf", "expected_pages": [6]},
    {"id": "Q04", "query": "How does masking work in the Transformer decoder?",                      "expected_paper": "attention_is_all_you_need_v7.pdf", "expected_pages": [3, 5]},
    {"id": "Q05", "query": "How are queries, keys, and values used in scaled dot-product attention?","expected_paper": "attention_is_all_you_need_v7.pdf", "expected_pages": [3, 4]},

    # Moshi
    {"id": "Q06", "query": "What is the main purpose of the Moshi model?",                          "expected_paper": "moshi_v1.pdf", "expected_pages": [1, 2, 3]},
    {"id": "Q07", "query": "How does Moshi support real-time speech interaction?",                   "expected_paper": "moshi_v1.pdf", "expected_pages": [1, 2, 3, 14]},
    {"id": "Q08", "query": "What role does the Mimi audio codec play in Moshi?",                    "expected_paper": "moshi_v1.pdf", "expected_pages": [9, 10, 11]},
    {"id": "Q09", "query": "What is the role of streaming in the Moshi architecture?",              "expected_paper": "moshi_v1.pdf", "expected_pages": [9, 10, 14]},
    {"id": "Q10", "query": "How does Moshi combine speech and text generation?",                    "expected_paper": "moshi_v1.pdf", "expected_pages": [15, 16]},

    # Dynamic Chunking
    {"id": "Q11", "query": "What is dynamic chunking in hierarchical sequence modeling?",            "expected_paper": "dynamic_chunking_v2.pdf", "expected_pages": [1, 2, 5, 6]},
    {"id": "Q12", "query": "Why is chunking used in hierarchical sequence modeling?",               "expected_paper": "dynamic_chunking_v2.pdf", "expected_pages": [1, 4, 5]},
    {"id": "Q13", "query": "How does dynamic chunking differ from fixed-size chunking?",            "expected_paper": "dynamic_chunking_v2.pdf", "expected_pages": [2, 5, 6, 7]},
    {"id": "Q14", "query": "How does the proposed method model hierarchical sequences?",            "expected_paper": "dynamic_chunking_v2.pdf", "expected_pages": [3, 4, 5, 6]},
    {"id": "Q15", "query": "What are the benefits of dynamic chunking for sequence modeling?",      "expected_paper": "dynamic_chunking_v2.pdf", "expected_pages": [1, 4, 5, 11]},
]


# --------------------------------------------------
# 3. Helper functions
# --------------------------------------------------

def normalize_name(name):
    return Path(str(name)).name.lower().strip()


def is_expected_paper(retrieved_paper, expected_paper):
    return (
        normalize_name(retrieved_paper) == normalize_name(expected_paper)
    )


def reciprocal_rank(results, expected_paper):
    for rank, item in enumerate(results, start=1):
        if is_expected_paper(item["paper"], expected_paper):
            return 1.0 / rank
    return 0.0


def calculate_page_hit(results, expected_pages):
    if not expected_pages:
        return ""
    expected_set = {int(p) for p in expected_pages}
    return int(
        any(int(item["page"]) in expected_set for item in results)
    )


# --------------------------------------------------
# 4. Initialize retriever
# --------------------------------------------------

print("\nInitializing Paper Retriever...")
retriever = PaperRetriever()

print(f"\nStarting retrieval evaluation...")
print(f"Questions: {len(questions)}")
print(f"Configurations: {SETTINGS}")


# --------------------------------------------------
# 5. Run evaluation
# --------------------------------------------------

evaluation_rows = []

for question in questions:

    print("\n" + "=" * 90)
    print(f"{question['id']}: {question['query']}")

    for setting in SETTINGS:

        start_time = time.perf_counter()

        results = retriever.search(
            question["query"],
            top_k=TOP_K,
            setting=setting,
            candidate_k=CANDIDATE_K,
            lambda_value=LAMBDA_VALUE
        )

        retrieval_ms = (time.perf_counter() - start_time) * 1000

        expected_paper = question["expected_paper"]

        paper_hits = [
            int(is_expected_paper(item["paper"], expected_paper))
            for item in results
        ]

        hit_at_1 = int(any(paper_hits[:1]))
        hit_at_3 = int(any(paper_hits[:3]))
        hit_at_5 = int(any(paper_hits[:5]))
        rr        = reciprocal_rank(results, expected_paper)
        page_hit  = calculate_page_hit(results, question["expected_pages"])

        retrieved_papers = ";".join(item["paper"] for item in results)
        retrieved_pages  = ";".join(str(item["page"]) for item in results)

        print(
            f"\n{setting} | Time: {retrieval_ms:.2f} ms "
            f"| Hit@1: {hit_at_1} | Hit@3: {hit_at_3} "
            f"| Hit@5: {hit_at_5} | RR: {rr:.3f}"
        )

        for rank, item in enumerate(results, start=1):
            print(
                f"{rank}. {item['paper']} "
                f"| Page {item['page']} "
                f"| Score: {item['score']:.4f}"
            )
            print(item["text"][:200])

        evaluation_rows.append({
            "question_id":      question["id"],
            "query":            question["query"],
            "setting":          setting,
            "expected_paper":   expected_paper,
            "expected_pages":   ";".join(map(str, question["expected_pages"])),
            "retrieved_papers": retrieved_papers,
            "retrieved_pages":  retrieved_pages,
            "hit_at_1":         hit_at_1,
            "hit_at_3":         hit_at_3,
            "hit_at_5":         hit_at_5,
            "page_hit_at_5":    page_hit,
            "reciprocal_rank":  round(rr, 4),
            "retrieval_ms":     round(retrieval_ms, 3),
        })


# --------------------------------------------------
# 6. Save detailed CSV
# --------------------------------------------------

evaluation_columns = [
    "question_id", "query", "setting", "expected_paper", "expected_pages",
    "retrieved_papers", "retrieved_pages",
    "hit_at_1", "hit_at_3", "hit_at_5", "page_hit_at_5",
    "reciprocal_rank", "retrieval_ms",
]

with open(EVALUATION_FILE, "w", newline="", encoding="utf-8") as file:
    writer = csv.DictWriter(file, fieldnames=evaluation_columns)
    writer.writeheader()
    writer.writerows(evaluation_rows)


# --------------------------------------------------
# 7. Aggregate per setting
# --------------------------------------------------

grouped = defaultdict(list)
for row in evaluation_rows:
    grouped[row["setting"]].append(row)


# --------------------------------------------------
# 8. Build & save summary CSV
# --------------------------------------------------

summary_rows = []

for setting in SETTINGS:

    rows = grouped[setting]

    hit1 = np.mean([row["hit_at_1"] for row in rows])
    hit3 = np.mean([row["hit_at_3"] for row in rows])
    hit5 = np.mean([row["hit_at_5"] for row in rows])
    mrr  = np.mean([row["reciprocal_rank"] for row in rows])
    times = [row["retrieval_ms"] for row in rows]

    page_rows = [row for row in rows if row["page_hit_at_5"] != ""]
    page_hit5 = (
        np.mean([int(row["page_hit_at_5"]) for row in page_rows])
        if page_rows else ""
    )

    summary_rows.append({
        "setting":               setting,
        "total_queries":         len(rows),
        "hit_at_1_percent":      round(hit1 * 100, 2),
        "hit_at_3_percent":      round(hit3 * 100, 2),
        "hit_at_5_percent":      round(hit5 * 100, 2),
        "mrr_percent":           round(mrr * 100, 2),
        "page_hit_at_5_percent": round(page_hit5 * 100, 2) if page_hit5 != "" else "",
        "avg_retrieval_ms":      round(float(np.mean(times)), 3),
        "p50_retrieval_ms":      round(float(np.percentile(times, 50)), 3),
        "p95_retrieval_ms":      round(float(np.percentile(times, 95)), 3),
    })

summary_columns = list(summary_rows[0].keys())

with open(SUMMARY_FILE, "w", newline="", encoding="utf-8") as file:
    writer = csv.DictWriter(file, fieldnames=summary_columns)
    writer.writeheader()
    writer.writerows(summary_rows)


# --------------------------------------------------
# 9. Print final summary
# --------------------------------------------------

print("\n" + "=" * 90)
print("RETRIEVAL EVALUATION SUMMARY")
print("=" * 90)

for row in summary_rows:
    print(f"\n{row['setting']}")
    print(f"Hit@1: {row['hit_at_1_percent']}%")
    print(f"Hit@3: {row['hit_at_3_percent']}%")
    print(f"Hit@5: {row['hit_at_5_percent']}%")
    print(f"MRR: {row['mrr_percent']}%")
    print(f"Average retrieval time: {row['avg_retrieval_ms']} ms")

print("\nEvaluation completed.")
print("Detailed results:", EVALUATION_FILE)
print("Summary results:", SUMMARY_FILE)
