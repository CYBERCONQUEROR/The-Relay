# Stage 3 — Stand-in Service

## Objective

A local stand-in API service was developed to simulate the operational behavior of a hosted language-model API. It enables repeatable experiments without repeatedly consuming the real API's rate limits or token quota.

## Simulated behaviors

- Requests-per-minute and tokens-per-minute limits.
- Daily token-budget exhaustion.
- Artificial latency and load-dependent degradation.
- Configurable concurrent request capacity.
- Random server failures and timeouts.
- Synthetic streaming responses.

## Initial calibration

The simulator's initial RPM and TPM settings were based on observed response headers from the Groq API during testing. The observed values were 1,000 RPM and 8,000 TPM. A single successful pipeline execution took approximately 3,029.7 ms and used 4,146 combined input and output tokens.

These are initial observations, not representative latency percentiles or confirmed daily quotas.

## Limitations

The stand-in is a controlled approximation, not a replica of Groq's infrastructure. Its token estimator is character-based, its failure probabilities are configurable, and its streaming output is synthetic. It does not reproduce real model inference, provider-side queueing, or every provider rate-limit policy.

## Outcome

The stand-in provides a controlled environment for testing steady traffic, bursts, sustained overload, quota exhaustion, latency degradation, and failure handling. Its outputs will be recorded separately from real Groq measurements.


## Initial Stage 3 Test Results

### Default configuration test

The stand-in service successfully processed a manual request.

| Metric | Result |
|---|---:|
| HTTP status | 200 |
| Input tokens | 10 |
| Output tokens | 250 |
| Total tokens | 260 |
| Latency | 1361.95 ms |
| Load multiplier | 1.0 |
| Source | Stand-in |

### Failure injection test

The simulator returned HTTP 503 with the message `Injected simulated server failure.`

This confirms that the configurable server-failure injection mechanism is operational.

The configured failure probability was 2%. Further repeated requests are required to estimate the observed failure rate.

### Interpretation

The default request confirms successful request processing and latency simulation. The HTTP 503 response confirms that the failure-injection path is functional. These are preliminary functional tests, not statistically meaningful load-test results.

### Concurrent Load Test — Default Configuration

A concurrent load test was performed using 15 requests and a thread pool of 10 workers.

| Metric                | Observed result |
| --------------------- | --------------: |
| Total requests        |              15 |
| Successful requests   |              15 |
| Rate-limited requests |               0 |
| Timed-out requests    |               0 |
| Failed requests       |               0 |
| Success rate          |            100% |

**Observation:** All 15 requests were processed successfully under the default simulator configuration. No rate limiting, timeout, or server failure was observed.

**Interpretation:** The default service configuration handled this small concurrent workload successfully. A larger workload is required to evaluate rate-limit enforcement, queueing, and degradation under sustained pressure.


### Low-RPM Limit Experiment

**Configuration:**
- Requests per minute: 5
- Tokens per minute: 8,000
- Maximum concurrent requests: 10
- Failure probability: 0%
- Timeout probability: 0%

**Observed results:**

| Metric | Result |
|---|---:|
| Total requests | 15 |
| Successful requests | 5 |
| Rate-limited requests | 10 |
| Timed-out requests | 0 |
| Other errors | 0 |
| Success rate | 33.33% |
| Rate-limit rate | 66.67% |

**Observation:** The simulator accepted five requests and returned HTTP 429 for the remaining ten requests.

**Conclusion:** The requests-per-minute enforcement mechanism behaved as expected under this test. The result demonstrates quota enforcement, not a measurement of real Groq throughput.


### TPM Limit Experiment

**Configuration:**
- RPM limit: 1,000
- TPM limit: 1,000
- Daily budget: 100,000
- Failure probability: 0%
- Timeout probability: 0%

**Observed results:**

| Metric | Result |
|---|---:|
| Total requests | 15 |
| Successful requests | 9 |
| Rate-limited requests | 6 |
| Timed-out requests | 0 |
| Other errors | 0 |

**Observation:** Nine requests were accepted before the rolling token budget was exhausted. The remaining six requests were rejected with HTTP 429.

**Conclusion:** The simulator's tokens-per-minute enforcement mechanism behaved as expected.

### Daily Token Budget Experiment

**Configuration:**
- RPM limit: 1,000
- TPM limit: 8,000
- Daily token budget: 1,000
- Failure probability: 0%
- Timeout probability: 0%

**Observed results:**

| Metric | Result |
|---|---:|
| Total requests | 15 |
| Successful requests | 9 |
| Rate-limited requests | 6 |
| Timed-out requests | 0 |
| Other errors | 0 |

**Observation:** Nine requests were accepted before the cumulative daily token budget was exhausted. The remaining six requests were rejected.

**Conclusion:** The simulator successfully enforced its cumulative daily token budget.



## Concurrent-load test

**Configuration:** Default simulator settings, including maximum concurrency of 10.

**Workload:** 15 concurrent requests.

| Metric | Result |
|---|---:|
| Total requests | 15 |
| Successful requests | 15 |
| Failed requests | 0 |
| Success rate | 100% |

**Observation:** All requests succeeded under this test. This does not establish unlimited concurrency capacity; it only describes the tested workload.

## Conclusion

The experiments demonstrate that RPM, TPM, and daily-budget restrictions can independently prevent requests from being served. The simulator provides a controlled way to test these conditions without repeatedly consuming the real Groq API quota.

**Important limitation:** These results are simulated. They must not be presented as measured Groq service limits or real production performance.