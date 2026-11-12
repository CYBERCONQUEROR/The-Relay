import time
import requests


BASE_URL = "http://127.0.0.1:8002"


def call_standin(prompt, scenario="manual", max_tokens=250, timeout=5):
    payload = {
        "prompt": prompt,
        "scenario": scenario,
        "max_tokens": max_tokens,
        "deadline_seconds": timeout,
    }
    start = time.perf_counter()

    try:
        response = requests.post(
            f"{BASE_URL}/simulate",
            json=payload,
            timeout=timeout + 1,
        )
        total_ms = (time.perf_counter() - start) * 1000

        try:
            data = response.json()
        except ValueError:
            data = {}

        if response.status_code == 429:
            status = "rate_limited"
        elif response.status_code == 504:
            status = "timeout"
        elif response.status_code == 503:
            status = "queue_rejected" if "queue is full" in str(data.get("detail", "")) else "error"
        elif response.status_code >= 500:
            status = "error"
        else:
            status = "ok"

        return {
            "status": status,
            "total_ms": round(total_ms, 2),
            "first_token_ms": None,
            "queue_wait_ms": data.get("queue_wait_ms"),
            "tokens_in": data.get("input_tokens", 0),
            "tokens_out": data.get("output_tokens", 0),
            "answer": data.get("answer"),
            "error": data.get("detail"),
            "source": "standin",
            "http_status": response.status_code,
            "retries": 0,
        }

    except requests.exceptions.Timeout:
        return {
            "status": "timeout",
            "total_ms": round((time.perf_counter() - start) * 1000, 2),
            "first_token_ms": None,
            "queue_wait_ms": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "answer": None,
            "error": "Client-side timeout",
            "source": "standin",
            "http_status": None,
            "retries": 0,
        }

    except requests.exceptions.RequestException as exc:
        return {
            "status": "error",
            "total_ms": round((time.perf_counter() - start) * 1000, 2),
            "first_token_ms": None,
            "queue_wait_ms": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "answer": None,
            "error": str(exc),
            "source": "standin",
            "http_status": None,
            "retries": 0,
        }


def call_standin_stream(prompt, scenario="manual", max_tokens=250, timeout=15):
    """Consume synthetic SSE and measure time to the first content token."""
    payload = {"prompt": prompt, "scenario": scenario, "max_tokens": max_tokens}
    start = time.perf_counter()
    first_token_ms = None
    chunks = []
    current_event = "message"

    try:
        with requests.post(
            f"{BASE_URL}/simulate/stream",
            json=payload,
            stream=True,
            timeout=timeout,
        ) as response:
            if response.status_code != 200:
                return {
                    "status": "rate_limited" if response.status_code == 429 else "error",
                    "first_token_ms": None,
                    "total_ms": round((time.perf_counter() - start) * 1000, 2),
                    "chunks": [],
                    "http_status": response.status_code,
                    "source": "standin",
                }

            for line in response.iter_lines(decode_unicode=True):
                if not line:
                    current_event = "message"
                    continue
                if line.startswith("event: "):
                    current_event = line[7:].strip()
                elif line.startswith("data: "):
                    data = line[6:]
                    if current_event == "message":
                        if first_token_ms is None:
                            first_token_ms = (time.perf_counter() - start) * 1000
                        chunks.append(data)

        return {
            "status": "ok",
            "first_token_ms": round(first_token_ms, 2) if first_token_ms is not None else None,
            "total_ms": round((time.perf_counter() - start) * 1000, 2),
            "chunks": chunks,
            "http_status": 200,
            "source": "standin",
        }

    except requests.exceptions.RequestException as exc:
        return {
            "status": "error",
            "first_token_ms": None,
            "total_ms": round((time.perf_counter() - start) * 1000, 2),
            "chunks": chunks,
            "error": str(exc),
            "source": "standin",
        }


if __name__ == "__main__":
    print(call_standin("Explain self-attention in the Transformer."))