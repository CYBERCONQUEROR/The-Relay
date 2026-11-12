# Stage 2 - Successful Pipeline Experiment

Date: 10 November 2026
Model: openai/gpt-oss-120b

Question:
How does the Transformer use self-attention?

Status: Successful
Verification: Passed

Retrieved passages: 3
Best retrieval score: 0.5521

Total latency: 3029.70 ms

Input tokens: 3216
Output tokens: 930
Total tokens: 4146

Retries: 0

Sources:
attention_is_all_you_need_v7.pdf, page 3
attention_is_all_you_need_v7.pdf, page 5

Observation:
The system successfully retrieved research passages,
generated a cited response and passed LLM verification.

Limitation:
Paragraph-level citation coverage was not initially
enforced. A deterministic check is being added.