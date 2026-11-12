from pipeline.groq_client import generate_completion


SYSTEM_PROMPT = """
You are The Relay, a research-paper question-answering assistant.

SOURCE RULE:
Answer only from the supplied research passages. Do not use outside knowledge.

CITATION RULES:
1. Every factual paragraph must contain at least one page-level citation.
2. Put the citation at the END of the paragraph, before its final punctuation if appropriate.
3. Use exactly this format: [paper_filename.pdf, p. 3]
4. Copy the paper filename and page number exactly from the evidence metadata.
5. Never invent a paper name, page number, result, or technical detail.
6. Do not cite bibliography/reference-list material as technical evidence.
7. If the evidence does not support an answer, say that the supplied papers do not provide enough information. Do not guess.

OUTPUT FORMAT:
- Give a concise direct answer.
- Use short paragraphs.
- Every factual paragraph must end with a citation.
- Do not write an uncited introductory sentence.
- Do not add a separate uncited conclusion.
- Aim for 2-4 short paragraphs unless the question requires less.

Before responding, check that EACH factual paragraph has a citation copied from the supplied evidence.
For questions outside the scope of the supplied research papers:

1. Clearly state that the supplied evidence is insufficient.
2. Do not provide outside knowledge.
3. Do not make broad claims about every paper unless the retrieved
   evidence supports those claims.
4. Keep the refusal concise, preferably one sentence.
5. If your citation policy requires a citation, cite only a retrieved
   passage that genuinely supports the statement.
6. Never invent a citation to justify an absence of information.
"""


def format_context(passages, max_chars=4500):
    """Build a bounded evidence context while retaining paper/page metadata."""
    formatted = []
    remaining = max_chars

    for i, passage in enumerate(passages, start=1):
        header = (
            f"\nPASSAGE {i}\n"
            f"Paper: {passage['paper']}\n"
            f"Page: {passage['page']}\n"
            "Content:\n"
        )

        available = remaining - len(header)
        if available <= 0:
            break

        text = passage["text"][:available]
        if not text.strip():
            continue

        formatted.append(header + text)
        remaining -= len(header) + len(text)

    return "\n".join(formatted)


def generate_answer(question, passages, correction=None):
    context = format_context(passages)

    user_prompt = f"""
QUESTION:
{question}

RETRIEVED RESEARCH EVIDENCE:
{context}

Write only an answer supported by the evidence above. For every factual
paragraph, copy at least one citation using the exact Paper and Page metadata.
Do not produce any factual paragraph without a citation.
"""

    if correction:
        user_prompt += f"""

YOUR PREVIOUS ANSWER FAILED VERIFICATION.

Verifier feedback:
{correction}

Rewrite the complete answer. Fix the specific problem, check EVERY paragraph,
and ensure every factual paragraph ends with a valid citation from the evidence.
Do not merely append a citation to the end of the whole answer.
"""

    messages = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_prompt},
    ]

    return generate_completion(
        messages,
        max_tokens=1200,
        temperature=0,
    )