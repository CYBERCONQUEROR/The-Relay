import os
import time
import re

from dotenv import load_dotenv

from retrieval.retriever import PaperRetriever
from pipeline.query_analyzer import analyze_query
from pipeline.answer_generator import generate_answer
from pipeline.answer_verifier import verify_answer
from pipeline.groq_client import GroqRequestError

load_dotenv()

TOP_K = int(os.getenv("RETRIEVAL_TOP_K", "8"))
RETRIEVAL_SETTING = os.getenv("RETRIEVAL_SETTING", "R4").upper()
CANDIDATE_K = int(os.getenv("RETRIEVAL_CANDIDATE_K", "50"))
MIN_SCORE = float(os.getenv("MIN_RETRIEVAL_SCORE", "0.25"))

retriever = PaperRetriever()


def _api_error_result(error, stage, start_time, totals):
    """Return a structured failure without treating an API outage as refusal."""
    total_ms = (time.perf_counter() - start_time) * 1000
    details = getattr(error, "details", {}) or {}

    return {
        "status": "api_error",
        "answer": (
            "The answer could not be generated because the language-model "
            "service was temporarily unavailable or its quota was exhausted. "
            "Please retry after the service limit resets."
        ),
        "sources": [],
        "error": {
            "stage": stage,
            "category": getattr(error, "category", "api_error"),
            "http_status": getattr(error, "status", None),
            "attempts": getattr(error, "attempts", 1),
            "api_retries": getattr(error, "retry_count", 0),
            "message": str(error),
            "rate_limit": details,
        },
        "metrics": {
            "total_ms": total_ms,
            "tokens_in": totals["tokens_in"],
            "tokens_out": totals["tokens_out"],
            "total_tokens": totals["tokens_in"] + totals["tokens_out"],
            "retries": totals["correction_retries"],
            "api_retries": totals["api_retries"],
            "generation_ms": totals["generation_ms"],
            "verification_ms": totals["verification_ms"],
            "retrieval_setting": RETRIEVAL_SETTING,
            "retrieval_top_k": TOP_K,
            "candidate_k": CANDIDATE_K,
        },
    }


def run_pipeline(question):
    start_time = time.perf_counter()

    totals = {
        "tokens_in": 0,
        "tokens_out": 0,
        "generation_ms": 0.0,
        "verification_ms": 0.0,
        "correction_retries": 0,
        "api_retries": 0,
    }

    # STEP 1: Analyze the question
    analysis = analyze_query(question)

    if not analysis["valid"]:
        return {
            "status": "invalid",
            "answer": analysis["reason"],
            "sources": [],
            "metrics": {},
        }

    question = analysis["question"]

    # STEP 2: Retrieve relevant passages
    passages = retriever.search(
        question,
        top_k=TOP_K,
        setting=RETRIEVAL_SETTING,
        candidate_k=CANDIDATE_K,
    )

    if not passages:
        return {
            "status": "refused_correctly",
            "answer": (
                "The provided research papers do not contain "
                "sufficient information to answer this question."
            ),
            "sources": [],
            "metrics": {
                "total_ms": (time.perf_counter() - start_time) * 1000,
                "tokens_in": 0,
                "tokens_out": 0,
                "retries": 0,
                "api_retries": 0,
            },
        }

    best_score = max(p["score"] for p in passages)

    # STEP 3: Retrieval confidence branch
    if best_score < MIN_SCORE:
        return {
            "status": "refused_correctly",
            "answer": (
                "I could not retrieve sufficiently relevant evidence from "
                "the provided research papers. Therefore, I cannot answer reliably."
            ),
            "sources": [],
            "metrics": {
                "total_ms": (time.perf_counter() - start_time) * 1000,
                "tokens_in": 0,
                "tokens_out": 0,
                "retries": 0,
                "api_retries": 0,
            },
        }

    # STEP 4: Generate and verify, with one correction attempt.
    # API retries happen inside groq_client; correction retries are separate.
    correction = None
    verified = False
    final_answer = None
    final_verdict = None
    attempt_records = []
    last_candidate = ""

    for attempt in range(2):
        generation_start = time.perf_counter()

        try:
            generation = generate_answer(
                question,
                passages,
                correction=correction,
            )
        except GroqRequestError as error:
            totals["generation_ms"] += (time.perf_counter() - generation_start) * 1000
            totals["api_retries"] += getattr(error, "retry_count", 0)
            return _api_error_result(error, "generation", start_time, totals)

        totals["generation_ms"] += (time.perf_counter() - generation_start) * 1000
        totals["tokens_in"] += generation.get("tokens_in", 0)
        totals["tokens_out"] += generation.get("tokens_out", 0)
        totals["api_retries"] += generation.get("api_retries", 0)

        candidate = generation.get("content", "").strip()

        if not candidate:
            total_ms = (time.perf_counter() - start_time) * 1000
            return {
                "status": "generation_failed",
                "answer": "",
                "candidate_answer": "",
                "attempts": attempt_records,
                "sources": [],
                "metrics": {
                    "total_ms": total_ms,
                    "tokens_in": totals["tokens_in"],
                    "tokens_out": totals["tokens_out"],
                    "total_tokens": totals["tokens_in"] + totals["tokens_out"],
                    "retries": totals["correction_retries"],
                    "api_retries": totals["api_retries"],
                    "generation_ms": totals["generation_ms"],
                    "verification_ms": totals["verification_ms"],
                    "retrieval_setting": RETRIEVAL_SETTING,
                    "retrieval_top_k": TOP_K,
                    "candidate_k": CANDIDATE_K,
                },
            }

        last_candidate = candidate
        attempt_record = {
            "attempt": attempt + 1,
            "candidate_answer": candidate,
            "generation": {
                "model": generation.get("model", ""),
                "finish_reason": generation.get("finish_reason", ""),
                "latency_ms": generation.get("latency_ms", 0),
                "tokens_in": generation.get("tokens_in", 0),
                "tokens_out": generation.get("tokens_out", 0),
                "total_tokens": generation.get("total_tokens", 0),
                "api_attempts": generation.get("api_attempts", 1),
                "api_retries": generation.get("api_retries", 0),
            },
        }

        # STEP 4B: Verify generated answer
        verification_start = time.perf_counter()

        try:
            verdict = verify_answer(question, candidate, passages)
        except GroqRequestError as error:
            totals["verification_ms"] += (time.perf_counter() - verification_start) * 1000
            totals["api_retries"] += getattr(error, "retry_count", 0)
            attempt_records.append(attempt_record)
            return _api_error_result(error, "verification", start_time, totals)

        totals["verification_ms"] += (time.perf_counter() - verification_start) * 1000
        totals["tokens_in"] += verdict.get("tokens_in", 0)
        totals["tokens_out"] += verdict.get("tokens_out", 0)
        totals["api_retries"] += verdict.get("api_retries", 0)

        final_verdict = verdict
        attempt_record["verification"] = {
            "verified": verdict.get("verified", False),
            "reason": verdict.get("reason", ""),
            "tokens_in": verdict.get("tokens_in", 0),
            "tokens_out": verdict.get("tokens_out", 0),
            "raw_response": verdict.get("raw_response", ""),
            "api_attempts": verdict.get("api_attempts", 1),
            "api_retries": verdict.get("api_retries", 0),
        }
        attempt_records.append(attempt_record)

        if verdict.get("verified", False):
            verified = True
            final_answer = candidate
            break

        if attempt == 0:
            totals["correction_retries"] += 1
            correction = verdict.get("reason", "")

    # STEP 5: Final decision
    total_ms = (time.perf_counter() - start_time) * 1000

    if not verified:
        return {
            "status": "verification_failed",
            "answer": (
                "I could not verify that the generated answer is sufficiently "
                "supported by the research papers. To avoid providing potentially "
                "incorrect information, I am declining to answer."
            ),
            "sources": [],
            "candidate_answer": last_candidate,
            "attempts": attempt_records,
            "verification": final_verdict,
            "metrics": {
                "total_ms": total_ms,
                "tokens_in": totals["tokens_in"],
                "tokens_out": totals["tokens_out"],
                "total_tokens": totals["tokens_in"] + totals["tokens_out"],
                "retries": totals["correction_retries"],
                "api_retries": totals["api_retries"],
                "generation_ms": totals["generation_ms"],
                "verification_ms": totals["verification_ms"],
                "retrieval_setting": RETRIEVAL_SETTING,
                "retrieval_top_k": TOP_K,
                "candidate_k": CANDIDATE_K,
            },
        }

    # Return only retrieved sources actually cited by the accepted answer.
    citation_pattern = re.compile(
        r"[\[【]\s*([^,\[\]【】]+?)\s*,\s*p\.\s*(\d+)\s*[\]】]",
        flags=re.IGNORECASE,
    )

    cited_pairs = {
        (re.sub(r"\s+", " ", paper).strip(), int(page))
        for paper, page in citation_pattern.findall(final_answer)
    }

    cited_sources = []
    seen_sources = set()

    for passage in passages:
        paper = re.sub(r"\s+", " ", str(passage.get("paper", ""))).strip()
        page = int(passage.get("page", 0))
        pair = (paper, page)

        if pair in cited_pairs and pair not in seen_sources:
            cited_sources.append({"paper": passage["paper"], "page": passage["page"]})
            seen_sources.add(pair)

    return {
        "status": "ok",
        "question": question,
        "answer": final_answer,
        "candidate_answer": last_candidate,
        "attempts": attempt_records,
        "sources": cited_sources,
        "verification": final_verdict,
        "metrics": {
            "total_ms": total_ms,
            "tokens_in": totals["tokens_in"],
            "tokens_out": totals["tokens_out"],
            "total_tokens": totals["tokens_in"] + totals["tokens_out"],
            "retries": totals["correction_retries"],
            "api_retries": totals["api_retries"],
            "generation_ms": totals["generation_ms"],
            "verification_ms": totals["verification_ms"],
            "retrieval_setting": RETRIEVAL_SETTING,
            "retrieval_top_k": TOP_K,
            "candidate_k": CANDIDATE_K,
            "retrieved_passages": len(passages),
            "best_retrieval_score": best_score,
        },
    }