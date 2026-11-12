import json
import re

from pipeline.groq_client import generate_completion


VERIFIER_PROMPT = """
You are a strict research evidence verifier for The Relay.

Compare the proposed answer only with the supplied research passages.

Check:
1. Every factual claim is supported by the evidence.
2. Every citation names a supplied paper and the exact page containing
   the cited passage.
3. Every factual paragraph has at least one page-level citation.
4. The answer contains no invented facts or unsupported additions.

Return ONLY one valid JSON object with this exact schema:
{
  "supported": true,
  "reason": "All factual claims and citations are supported",
  "unsupported_claims": []
}

Use supported=false if any factual claim is unsupported, a citation is wrong,
or a factual paragraph has no citation.

Do not use Markdown, code fences, or text outside the JSON object.
"""


# Accept both ASCII square brackets [] and GPT-style Unicode brackets 【】.
# Both represent the same citation format for validation purposes.
CITATION_PATTERN = re.compile(
    r"[\[【]([^,\[\]【】]+),\s*p\.\s*(\d+)[\]】]"
)

def validate_citations(answer, passages):

    available = {
        (p["paper"].strip(), int(p["page"]))
        for p in passages
    }

    citations = CITATION_PATTERN.findall(answer)

    print("Extracted citations:", citations)
    print("Available citations:", available)

    if not citations:
        return False, (
            "No valid page-level citations found. "
            "Expected [paper_filename.pdf, p. PAGE]."
        )

    for paper, page in citations:
        citation = (paper.strip(), int(page))

        if citation not in available:
            return False, f"Citation does not match retrieved evidence: {citation}"

    return True, "All citations match retrieved evidence."


def validate_paragraph_citations(answer):

    paragraphs = [
        p.strip()
        for p in re.split(r"\n\s*\n", answer.strip())
        if p.strip()
    ]

    for index, paragraph in enumerate(paragraphs, start=1):

        if not CITATION_PATTERN.search(paragraph):
            return False, f"Paragraph {index} has no page-level citation."

    return True, "Every paragraph contains a citation."


def _local_failure(reason):
    return {
        "verified": False,
        "reason": reason,
        "unsupported_claims": [],
        "tokens_in": 0,
        "tokens_out": 0,
        "raw_response": "",
        "finish_reason": "local_validation",
        "verifier_latency_ms": 0,
        "api_attempts": 0,
        "api_retries": 0,
    }


def verify_answer(question, answer, passages):
    citation_valid, citation_reason = validate_citations(answer, passages)
    if not citation_valid:
        return _local_failure(citation_reason)

    coverage_valid, coverage_reason = validate_paragraph_citations(answer)
    if not coverage_valid:
        return _local_failure(coverage_reason)

    # Keep verifier evidence bounded to reduce TPM consumption.
    evidence_parts = []
    remaining_chars = 4500

    for passage in passages:
        header = (
            f"Paper: {passage['paper']}\n"
            f"Page: {passage['page']}\n"
            "Text:\n"
        )
        available = remaining_chars - len(header)
        if available <= 0:
            break

        passage_text = passage["text"][:available]
        evidence_parts.append(header + passage_text)
        remaining_chars -= len(header) + len(passage_text)

    evidence = "\n\n".join(evidence_parts)

    messages = [
        {"role": "system", "content": VERIFIER_PROMPT},
        {
            "role": "user",
            "content": f"""
Question:
{question}

Research evidence:
{evidence}

Proposed answer:
{answer}

Return the required JSON verdict.
""",
        },
    ]

    result = generate_completion(
        messages,
        max_tokens=1200,
        temperature=0,
    )

    raw_response = result.get("content", "") or ""
    if result.get("finish_reason") == "length" or not raw_response.strip():
        return {
            "verified": False,
            "reason": (
                "Verifier response incomplete or empty. "
                f"Finish reason: {result.get('finish_reason')}"
            ),
            "unsupported_claims": [],
            "tokens_in": result.get("tokens_in", 0),
            "tokens_out": result.get("tokens_out", 0),
            "raw_response": raw_response,
            "finish_reason": result.get("finish_reason", ""),
            "verifier_latency_ms": result.get("latency_ms", 0),
            "api_attempts": result.get("api_attempts", 1),
            "api_retries": result.get("api_retries", 0),
        }
    common = {
        "tokens_in": result.get("tokens_in", 0),
        "tokens_out": result.get("tokens_out", 0),
        "raw_response": raw_response,
        "finish_reason": result.get("finish_reason", ""),
        "verifier_latency_ms": result.get("latency_ms", 0),
        "api_attempts": result.get("api_attempts", 1),
        "api_retries": result.get("api_retries", 0),
    }

    try:
        content = raw_response.strip()
        content = re.sub(r"^```(?:json)?\s*|\s*```$", "", content).strip()
        verdict = json.loads(content)

        if not isinstance(verdict, dict):
            raise ValueError("Expected JSON object")
        if not isinstance(verdict.get("supported"), bool):
            raise ValueError("Missing boolean supported field")
        if not isinstance(verdict.get("reason"), str):
            raise ValueError("Missing reason field")
        if not isinstance(verdict.get("unsupported_claims"), list):
            raise ValueError("Invalid unsupported_claims field")

        return {
            "verified": verdict["supported"],
            "reason": verdict["reason"],
            "unsupported_claims": verdict["unsupported_claims"],
            **common,
        }

    except (json.JSONDecodeError, ValueError, TypeError) as error:
        return {
            "verified": False,
            "reason": "Verifier returned invalid JSON: " + str(error),
            "unsupported_claims": [],
            **common,
        }