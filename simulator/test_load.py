
from concurrent.futures import ThreadPoolExecutor, as_completed
from simulator.standin_client import call_standin


PROMPT = "Explain how multi-head attention works."


def send_request(request_number):
    return request_number, call_standin(
        prompt=PROMPT,
        scenario="concurrent_test",
        max_tokens=100,
        timeout=5
    )


if __name__ == "__main__":
    number_of_requests = 15

    results = []

    start_message = (
        f"Sending {number_of_requests} concurrent requests..."
    )
    print(start_message)

    with ThreadPoolExecutor(max_workers=10) as executor:
        futures = [
            executor.submit(send_request, i)
            for i in range(number_of_requests)
        ]

        for future in as_completed(futures):
            request_id, result = future.result()
            results.append(result)

            print(
                f"Request {request_id}: "
                f"status={result['status']}, "
                f"latency={result['total_ms']} ms"
            )

    successful = sum(r["status"] == "ok" for r in results)
    limited = sum(r["status"] == "rate_limited" for r in results)
    timed_out = sum(r["status"] == "timeout" for r in results)
    errors = sum(r["status"] == "error" for r in results)

    print("\n----- LOAD TEST SUMMARY -----")
    print("Total requests:", len(results))
    print("Successful:", successful)
    print("Rate limited:", limited)
    print("Timed out:", timed_out)
    print("Errors:", errors)
