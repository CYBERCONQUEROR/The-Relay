
# Groq Real Service Observation 001

Date: 10 November 2026
Model: openai/gpt-oss-120b
Service tier: on_demand

## Rate limit observation

HTTP status: 429
Error code: rate_limit_exceeded

TPM limit: 8000
Tokens used: 5847
Tokens requested: 2900
Tokens remaining: 2153

RPM limit: 1000
Requests remaining: 998

Retry-After: 6 seconds
Token reset header: 43.852 seconds

## Finding

The request was rejected because the combined
token usage exceeded the available TPM capacity.

Observed projected usage: 8747 tokens.
Observed capacity: 8000 tokens.
Excess: 747 tokens.

## Engineering decision

Reduce retrieval context and output limits.
Respect Retry-After.
Avoid uncontrolled retries.
Use a stand-in service for heavy-load experiments.

## Limitations

Daily limits not measured.
Concurrent request limit not measured.
Long-term stability of limits not established.
