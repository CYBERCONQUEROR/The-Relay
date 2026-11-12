import csv
import json
import os
import time
from pathlib import Path
import re

from dotenv import load_dotenv
from pipeline.orchestrator import run_pipeline

load_dotenv()

OUTPUT_DIR = Path("results")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
OUTPUT_FILE = OUTPUT_DIR / "answer_quality_evaluation78.csv"

# 15 in-scope questions plus 5 deliberately out-of-scope questions.
# The out-of-scope questions test whether the system refuses unsupported requests.
TEST_CASES = [
    # ("A01", "How does self-attention work in the Transformer?", "answer"),
    #  ("A02", "What is the purpose of multi-head attention?", "answer"),
    # ("A03", "Why does the Transformer use positional encoding?", "answer"),
    # ("A04", "How does masking work in the Transformer decoder?", "answer"),
    # ("A05", "How are queries, keys, and values used in scaled dot-product attention?", "answer"),
    #  ("A06", "What is the main purpose of the Moshi model?", "answer"),
    # ("A07", "How does Moshi support real-time speech interaction?", "answer"),
    # ("A08", "What role does the Mimi audio codec play in Moshi?", "answer"),
    # ("A09", "What is the role of streaming in the Moshi architecture?", "answer"),
    # ("A10", "How does Moshi combine speech and text generation?", "answer")
    # ("A11", "What is dynamic chunking in hierarchical sequence modeling?", "answer"),
    # ("A12", "Why is chunking used in hierarchical sequence modeling?", "answer")
    # ("A13", "How does dynamic chunking differ from fixed-size chunking?", "answer"),
    # ("A14", "How does the proposed method model hierarchical sequences?", "answer"),
    # ("A15", "What are the benefits of dynamic chunking for sequence modeling?", "answer"),
    # ("U01", "Who won the 2024 Nobel Prize in Physics?", "refuse"),
    # ("U02", "What is the current price of Bitcoin?", "refuse")
    # ("U03", "What is the capital of Australia?", "refuse")
    # ("U04", "Give me a medical diagnosis for a persistent headache.", "refuse"),
    ("U05", "What is the weather forecast for tomorrow in Ghaziabad?", "refuse")
]

FIELDS = [
    "case_id", "question", "expected_behavior", "actual_status",
    "behavior_match", "answer_grade", "citation_grade",
    "answer", "sources", "verified", "verification_reason",
    "tokens_in", "tokens_out", "total_tokens", "retries",
    "retrieved_passages", "retrieval_setting", "retrieval_top_k",
    "candidate_k", "generation_ms", "verification_ms", "total_ms",
    "ttft_ms", "candidate_answer", "attempts_json",
    "http_status", "retry_after", "rate_limit_limit_tokens",
    "rate_limit_remaining_tokens", "rate_limit_reset_tokens",
    "rate_limit_limit_requests", "rate_limit_remaining_requests", "notes"
]

rows = []
for index, (case_id, question, expected) in enumerate(TEST_CASES, start=1):
    print(f"\n[{index}/{len(TEST_CASES)}] {case_id}: {question}")
    if index > 1:
        wait_seconds = int(os.getenv("EVALUATION_DELAY_SECONDS"))
        print(f"Waiting {wait_seconds} seconds before the next case...")
        time.sleep(wait_seconds)
    started = time.perf_counter()

    try:
        result = run_pipeline(question)
        metrics = result.get("metrics", {})
        status = result.get("status", "error")
        verification = result.get("verification") or {}
        answer = (result.get("answer") or "").strip()

        refusal_phrases = [
            "do not provide enough information",
            "does not provide enough information",
            "do not provide enough data",
            "does not provide enough data",
            "not enough information to answer",
            "not enough data to answer",
            "cannot answer from the supplied",
            "cannot answer from the provided",
            "cannot be determined from the provided",
            "cannot be determined from the supplied",
            "not mentioned in the supplied",
            "not mentioned in the provided",
            "not found in the provided",
            "the supplied passages do not mention",
            "the provided passages do not mention",
            "no information about",
            "insufficient information",
            "insufficient evidence",
            "do not contain any information",
            "does not contain any information",
            "contain no information about",
            "contains no information about",
            "do not contain enough information",
            "does not contain enough information",
            "do not have information about",
            "no relevant information",
            "do not provide any information",
            "does not provide any information",
            "do not contain information sufficient",
            "does not contain sufficient information",
            "not sufficient to provide",
            "insufficient to provide",
        ]

        answer_lower = answer.casefold()
        is_abstention = any(
            phrase in answer_lower for phrase in refusal_phrases
        )

        if status == "refused_correctly":
            actual_behavior = "refuse"
        elif status == "ok":
            actual_behavior = "refuse" if is_abstention else "answer"
        else:
            actual_behavior = "error"

        # API/pipeline errors are not behavior evaluations.
        behavior_match = (
            int(actual_behavior == expected)
            if actual_behavior != "error"
            else ""
        )

        rows.append({
            "case_id": case_id,
            "question": question,
            "expected_behavior": expected,
            "actual_status": status,
            "behavior_match": behavior_match,
            "answer_grade": "",       # Manual: correct / partially_correct / incorrect
            "citation_grade": "",     # Manual: correct / partially_correct / incorrect
            "answer": result.get("answer", ""),
            "candidate_answer": result.get("candidate_answer", ""),
            "attempts_json": json.dumps(result.get("attempts", []), ensure_ascii=False),
            "http_status": (result.get("error_details") or {}).get("status", ""),
            "retry_after": (result.get("error_details") or {}).get("retry_after", ""),
            "rate_limit_limit_tokens": (result.get("error_details") or {}).get("limit_tokens", ""),
            "rate_limit_remaining_tokens": (result.get("error_details") or {}).get("remaining_tokens", ""),
            "rate_limit_reset_tokens": (result.get("error_details") or {}).get("reset_tokens", ""),
            "rate_limit_limit_requests": (result.get("error_details") or {}).get("limit_requests", ""),
            "rate_limit_remaining_requests": (result.get("error_details") or {}).get("remaining_requests", ""),
            "sources": json.dumps(result.get("sources", []), ensure_ascii=False),
            "verified": verification.get("verified", ""),
            "verification_reason": verification.get("reason", ""),
            "tokens_in": metrics.get("tokens_in", 0),
            "tokens_out": metrics.get("tokens_out", 0),
            "total_tokens": metrics.get("total_tokens",
                                        metrics.get("tokens_in", 0) + metrics.get("tokens_out", 0)),
            "retries": metrics.get("retries", 0),
            "retrieved_passages": metrics.get("retrieved_passages", ""),
            "retrieval_setting": metrics.get("retrieval_setting", os.getenv("RETRIEVAL_SETTING", "R4")),
            "retrieval_top_k": metrics.get("retrieval_top_k", os.getenv("RETRIEVAL_TOP_K", "8")),
            "candidate_k": metrics.get("candidate_k", os.getenv("RETRIEVAL_CANDIDATE_K", "50")),
            "generation_ms": metrics.get("generation_ms", ""),
            "verification_ms": metrics.get("verification_ms", ""),
            "total_ms": metrics.get("total_ms", round((time.perf_counter() - started) * 1000, 3)),
            "ttft_ms": "not_measured",
            "notes": ""
        })
        print("Status:", status, "| Tokens:", rows[-1]["total_tokens"],
              "| Time:", rows[-1]["total_ms"], "ms")
    except Exception as exc:
        rows.append({
            "case_id": case_id, "question": question,
            "expected_behavior": expected, "actual_status": "error",
            "behavior_match": "", "answer_grade": "", "citation_grade": "",
            "answer": "", "candidate_answer": "", "attempts_json": "[]",
            "http_status": getattr(exc, "status_code", ""),
            "retry_after": "", "rate_limit_limit_tokens": "",
            "rate_limit_remaining_tokens": "", "rate_limit_reset_tokens": "",
            "rate_limit_limit_requests": "", "rate_limit_remaining_requests": "",
            "sources": "[]", "verified": False,
            "verification_reason": repr(exc), "tokens_in": "", "tokens_out": "",
            "total_tokens": "", "retries": "", "retrieved_passages": "",
            "retrieval_setting": os.getenv("RETRIEVAL_SETTING", "R4"),
            "retrieval_top_k": os.getenv("RETRIEVAL_TOP_K", "8"),
            "candidate_k": os.getenv("RETRIEVAL_CANDIDATE_K", "50"),
            "generation_ms": "", "verification_ms": "",
            "total_ms": round((time.perf_counter() - started) * 1000, 3),
            "ttft_ms": "not_measured", "notes": repr(exc)
        })
        print("ERROR:", repr(exc))

    # Checkpoint after each case so an interrupted run retains completed results.
    with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(rows)

with OUTPUT_FILE.open("w", newline="", encoding="utf-8") as f:
    writer = csv.DictWriter(f, fieldnames=FIELDS)
    writer.writeheader()
    writer.writerows(rows)

print(f"\nSaved {len(rows)} rows to {OUTPUT_FILE}")

evaluated = [
    row for row in rows
    if str(row.get("behavior_match", "")).strip() in {"0", "1"}
]
matches = sum(int(row["behavior_match"]) for row in evaluated)
api_errors = sum(row.get("actual_status") == "error" for row in rows)

print("Behavior matches:", matches, "/", len(evaluated))
print("Behavior mismatches:", len(evaluated) - matches)
print("API/pipeline errors:", api_errors)
print("Fill answer_grade and citation_grade manually after inspecting answers.")