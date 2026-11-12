
import csv
import os
import time
import statistics
import requests

from datetime import datetime, timezone
from concurrent.futures import ThreadPoolExecutor, as_completed


BASE_URL = "http://127.0.0.1:8002"
RESULTS_DIR = "results"
CSV_PATH = os.path.join(RESULTS_DIR, "results1.csv")
PEAK_QUEUE_CSV_PATH = os.path.join(RESULTS_DIR, "peak_queue_summary.csv")

os.makedirs(RESULTS_DIR, exist_ok=True)

CSV_COLUMNS = [
    "request_id",
    "scenario",
    "source",
    "start_time",
    "first_token_ms",
    "total_ms",
    "status",
    "retries",
    "tokens_in",
    "tokens_out",
    "retrieval_setting",
    "answer_grade"
]

PROMPT = "Explain self-attention in the Transformer."


def configure(**settings):
    response = requests.post(
        f"{BASE_URL}/configure",
        json=settings,
        timeout=10
    )
    response.raise_for_status()
    print("Configuration:", response.json())


def reset():
    response = requests.post(
        f"{BASE_URL}/reset",
        timeout=10
    )
    response.raise_for_status()
    print("Simulator reset.")



def get_metrics():
    response = requests.get(f"{BASE_URL}/metrics", timeout=10)
    response.raise_for_status()
    return response.json()


def wait_until_drained(max_wait_seconds=90):
    """
    Wait until the simulator has no active or queued requests.
    This matters because a client-side timeout does not necessarily
    mean the stand-in server has stopped processing that request.
    """
    deadline = time.time() + max_wait_seconds

    while time.time() < deadline:
        metrics = get_metrics()
        active = metrics.get("active_requests", 0)
        queued = metrics.get("queued_requests", 0)

        if active == 0 and queued == 0:
            return metrics

        time.sleep(1)

    # Return the latest snapshot even if the server did not drain in time.
    return get_metrics()


def capture_peak_queue(scenario):
    metrics = wait_until_drained()

    peak = metrics.get("peak_queue_length")
    active = metrics.get("active_requests")
    queued = metrics.get("queued_requests")
    rejected = metrics.get("total_queue_rejections")

    print(f"Peak queue for {scenario}: {peak}")
    print(f"Final active requests: {active}; queued requests: {queued}")
    print(f"Queue rejections: {rejected}")

    return {
        "scenario": scenario,
        "peak_queue_length": peak if peak is not None else "not_measured",
        "total_queue_rejections": rejected if rejected is not None else "not_measured",
        "active_requests_at_capture": active if active is not None else "not_measured",
        "queued_requests_at_capture": queued if queued is not None else "not_measured",
    }


def save_peak_queue_summary(records):
    with open(PEAK_QUEUE_CSV_PATH, "w", newline="", encoding="utf-8") as file:
        columns = [
            "scenario",
            "peak_queue_length",
            "total_queue_rejections",
            "active_requests_at_capture",
            "queued_requests_at_capture",
        ]
        writer = csv.DictWriter(file, fieldnames=columns)
        writer.writeheader()
        writer.writerows(records)

    print(f"Saved peak queue measurements to {PEAK_QUEUE_CSV_PATH}")


def send_request(request_id, scenario, max_tokens=100, delay=0):
    if delay:
        time.sleep(delay)

    start_time = datetime.now(timezone.utc).isoformat()
    start = time.perf_counter()

    tokens_in = max(1, len(PROMPT) // 4)

    row = {
        "request_id": request_id,
        "scenario": scenario,
        "source": "standin",
        "start_time": start_time,
        "first_token_ms": "",
        "total_ms": "",
        "status": "error",
        "retries": 0,
        "tokens_in": tokens_in,
        "tokens_out": max_tokens,
        "retrieval_setting": "not_applicable",
        "answer_grade": ""
    }

    
    try:
        response = requests.post(
            f"{BASE_URL}/simulate",
            json={
                "prompt": PROMPT,
                "max_tokens": max_tokens,
                "scenario": scenario
            },
            timeout=5
        )

        if response.status_code == 429:
            row["status"] = "rate_limited"
            row["tokens_in"] = 0
            row["tokens_out"] = 0

        elif response.status_code == 504:
            row["status"] = "timeout"
            row["tokens_in"] = 0
            row["tokens_out"] = 0

        elif response.status_code >= 500:
            row["status"] = "error"
            row["tokens_in"] = 0
            row["tokens_out"] = 0

        elif response.ok:
            row["status"] = "ok"
            data = response.json()
            row["tokens_in"] = data.get("input_tokens", 0)
            row["tokens_out"] = data.get("output_tokens", 0)

        else:
            row["status"] = "error"
            row["tokens_in"] = 0
            row["tokens_out"] = 0

    except requests.exceptions.Timeout:
        row["status"] = "timeout"
        row["tokens_in"] = 0
        row["tokens_out"] = 0

    except requests.exceptions.RequestException as exc:
        print(f"Request {request_id} error: {exc}")
        row["status"] = "error"
        row["tokens_in"] = 0
        row["tokens_out"] = 0

    finally:
        row["total_ms"] = round(
            (time.perf_counter() - start) * 1000, 2
        )


    return row


def run_scenario(
    scenario,
    total_requests,
    workers,
    interval=0,
    max_tokens=100
):
    print(f"\nStarting scenario: {scenario}")
    print(f"Requests: {total_requests}, workers: {workers}")

    rows = []

    with ThreadPoolExecutor(max_workers=workers) as executor:
        futures = []

        for i in range(total_requests):
            futures.append(
                executor.submit(
                    send_request,
                    f"{scenario}_{i + 1}",
                    scenario,
                    max_tokens,
                    i * interval
                )
            )

        for future in as_completed(futures):
            rows.append(future.result())

    rows.sort(key=lambda x: x["request_id"])

    print_summary(scenario, rows)

    return rows


def print_summary(scenario, rows):
    total = len(rows)

    counts = {
        status: sum(row["status"] == status for row in rows)
        for status in ["ok", "rate_limited", "timeout", "error"]
    }

    latencies = [
        row["total_ms"]
        for row in rows
        if row["total_ms"] != ""
    ]

    print(f"\n----- {scenario.upper()} SUMMARY -----")
    print("Total requests:", total)
    print("Successful:", counts["ok"])
    print("Rate limited:", counts["rate_limited"])
    print("Timed out:", counts["timeout"])
    print("Errors:", counts["error"])

    if latencies:
        print("Mean latency:", round(statistics.mean(latencies), 2), "ms")
        print("Median latency:", round(statistics.median(latencies), 2), "ms")



def save_results(rows):
    with open(CSV_PATH, "w", newline="", encoding="utf-8") as file:
        writer = csv.DictWriter(
            file,
            fieldnames=CSV_COLUMNS
        )
        writer.writeheader()
        writer.writerows(rows)

    print(f"\nSaved {len(rows)} rows to {CSV_PATH}")



def run_all_experiments():
    all_rows = []
    peak_queue_records = []

    # -------------------------------------------------
    # 1. STEADY TRAFFIC
    # -------------------------------------------------
    reset()
    configure(
        requests_per_minute=1000,
        tokens_per_minute=12000,
        daily_token_budget=100000,
        base_latency_ms=300,
        latency_jitter_ms=50,
        failure_probability=0.0,
        timeout_probability=0.0
    )

    rows = run_scenario(
        scenario="steady",
        total_requests=20,
        workers=5,
        interval=0.5,
        max_tokens=100
    )
    all_rows.extend(rows)
    peak_queue_records.append(capture_peak_queue("steady"))

    # -------------------------------------------------
    # 2. BURST TRAFFIC
    # -------------------------------------------------
    reset()
    configure(
        requests_per_minute=1000,
        tokens_per_minute=12000,
        daily_token_budget=100000,
        base_latency_ms=500,
        latency_jitter_ms=100,
        failure_probability=0.0,
        timeout_probability=0.0
    )

    rows = run_scenario(
        scenario="burst",
        total_requests=30,
        workers=30,
        interval=0,
        max_tokens=100
    )
    all_rows.extend(rows)
    peak_queue_records.append(capture_peak_queue("burst"))

    # -------------------------------------------------
    # 3. SUSTAINED OVERLOAD
    # -------------------------------------------------
    reset()
    configure(
        requests_per_minute=1000,
        tokens_per_minute=6000,
        daily_token_budget=100000,
        base_latency_ms=300,
        latency_jitter_ms=50,
        failure_probability=0.0,
        timeout_probability=0.0,
        degradation_start_ratio=0.70,
        max_degradation_multiplier=3.0
    )

    rows = run_scenario(
        scenario="sustained_overload",
        total_requests=100,
        workers=20,
        interval=0,
        max_tokens=100
    )
    all_rows.extend(rows)
    peak_queue_records.append(capture_peak_queue("sustained_overload"))

    # -------------------------------------------------
    # 4. HALVED RPM LIMIT
    # -------------------------------------------------
    reset()
    configure(
        requests_per_minute=500,
        tokens_per_minute=1000000,
        daily_token_budget=1000000,
        base_latency_ms=10,
        latency_jitter_ms=0,
        failure_probability=0.0,
        timeout_probability=0.0
    )

    rows = run_scenario(
        scenario="rpm_halved",
        total_requests=510,
        workers=50,
        interval=0,
        max_tokens=1
    )
    all_rows.extend(rows)
    peak_queue_records.append(capture_peak_queue("rpm_halved"))

    # -------------------------------------------------
    # 5. DAILY BUDGET EXHAUSTION
    # -------------------------------------------------
    reset()
    configure(
        requests_per_minute=1000,
        tokens_per_minute=8000,
        daily_token_budget=1000,
        base_latency_ms=10,
        latency_jitter_ms=0,
        failure_probability=0.0,
        timeout_probability=0.0
    )

    rows = run_scenario(
        scenario="daily_budget_exhausted",
        total_requests=15,
        workers=15,
        interval=0,
        max_tokens=100
    )
    all_rows.extend(rows)
    peak_queue_records.append(capture_peak_queue("daily_budget_exhausted"))

    save_results(all_rows)
    save_peak_queue_summary(peak_queue_records)

    print("\nALL EXPERIMENTS COMPLETED.")


if __name__ == "__main__":
    run_all_experiments()