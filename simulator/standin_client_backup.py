
import time
import requests


BASE_URL = "http://127.0.0.1:8002"


def call_standin(
    prompt,
    scenario="manual",
    max_tokens=250,
    timeout=5
):
    payload = {
        "prompt": prompt,
        "scenario": scenario,
        "max_tokens": max_tokens
    }

    start = time.perf_counter()

    try:
        response = requests.post(
            f"{BASE_URL}/simulate",
            json=payload,
            timeout=timeout
        )

        total_ms = (time.perf_counter() - start) * 1000

        if response.status_code == 429:
            status = "rate_limited"
        elif response.status_code == 504:
            status = "timeout"
        elif response.status_code >= 500:
            status = "error"
        else:
            status = "ok"

        data = response.json()

        return {
            "status": status,
            "total_ms": round(total_ms, 2),
            "first_token_ms": None,
            "tokens_in": data.get("input_tokens", 0),
            "tokens_out": data.get("output_tokens", 0),
            "answer": data.get("answer"),
            "error": data.get("detail"),
            "source": "standin"
        }

    except requests.exceptions.Timeout:
        total_ms = (time.perf_counter() - start) * 1000

        return {
            "status": "timeout",
            "total_ms": round(total_ms, 2),
            "first_token_ms": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "answer": None,
            "error": "Client-side timeout",
            "source": "standin"
        }

    except requests.exceptions.RequestException as exc:
        total_ms = (time.perf_counter() - start) * 1000

        return {
            "status": "error",
            "total_ms": round(total_ms, 2),
            "first_token_ms": None,
            "tokens_in": 0,
            "tokens_out": 0,
            "answer": None,
            "error": str(exc),
            "source": "standin"
        }


if __name__ == "__main__":
    result = call_standin(
        "Explain self-attention in the Transformer.",
        scenario="manual"
    )

    print(result)
