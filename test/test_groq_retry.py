"""
Offline tests for pipeline/groq_client.py retry behaviour.
No real Groq API requests are sent. All API responses are mocked.
Run from THE_RELAY project root:
    python tests/test_groq_retry.py
"""

import os
import sys
import unittest
from unittest.mock import patch, Mock

# Ensure project root is importable when this file is run directly.
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if PROJECT_ROOT not in sys.path:
    sys.path.insert(0, PROJECT_ROOT)

from groq import APIStatusError
from httpx import Request, Response

from pipeline import groq_client


def make_status_error(status_code, message, headers=None):
    request = Request("POST", "https://api.groq.com/openai/v1/chat/completions")
    response = Response(
        status_code,
        headers=headers or {},
        request=request,
        json={"error": {"message": message}},
    )
    return APIStatusError(message, response=response, body={"error": {"message": message}})


class GroqRetryPolicyTests(unittest.TestCase):

    def setUp(self):
        self.messages = [{"role": "user", "content": "test"}]
        self.success_response = Mock()
        self.success_response.choices = [
            Mock(finish_reason="stop", message=Mock(content="Mock answer"))
        ]
        self.success_response.model = "mock-model"
        self.success_response.usage = Mock(
            prompt_tokens=10,
            completion_tokens=5,
            total_tokens=15,
        )

    @patch("pipeline.groq_client.time.sleep")
    def test_temporary_429_retries_then_succeeds(self, sleep_mock):
        temporary_429 = make_status_error(
            429,
            "Rate limit reached. Please retry.",
            {"retry-after": "0"},
        )

        with patch.object(
            groq_client.client.chat.completions,
            "create",
            side_effect=[temporary_429, self.success_response],
        ) as create_mock:
            result = groq_client.generate_completion(self.messages)

        self.assertEqual(create_mock.call_count, 2)
        self.assertEqual(result["api_attempts"], 2)
        self.assertEqual(result["api_retries"], 1)
        print("PASS: temporary 429 retried and then succeeded")

    @patch("pipeline.groq_client.time.sleep")
    def test_daily_quota_exhaustion_stops_without_retry(self, sleep_mock):
        daily_429 = make_status_error(
            429,
            "Tokens per day (TPD) limit reached.",
        )

        with patch.object(
            groq_client.client.chat.completions,
            "create",
            side_effect=daily_429,
        ) as create_mock:
            with self.assertRaises(groq_client.GroqRequestError) as caught:
                groq_client.generate_completion(self.messages)

        self.assertEqual(create_mock.call_count, 1)
        self.assertEqual(caught.exception.category, "daily_quota_exhausted")
        self.assertEqual(caught.exception.retry_count, 0)
        sleep_mock.assert_not_called()
        print("PASS: daily quota exhaustion stopped immediately")

    @patch("pipeline.groq_client.time.sleep")
    def test_503_retries_then_succeeds(self, sleep_mock):
        server_error = make_status_error(503, "Service unavailable")

        with patch.object(
            groq_client.client.chat.completions,
            "create",
            side_effect=[server_error, self.success_response],
        ) as create_mock:
            result = groq_client.generate_completion(self.messages)

        self.assertEqual(create_mock.call_count, 2)
        self.assertEqual(result["api_retries"], 1)
        print("PASS: 503 retried and then succeeded")

    @patch("pipeline.groq_client.time.sleep")
    def test_401_stops_immediately(self, sleep_mock):
        auth_error = make_status_error(401, "Invalid API key")

        with patch.object(
            groq_client.client.chat.completions,
            "create",
            side_effect=auth_error,
        ) as create_mock:
            with self.assertRaises(groq_client.GroqRequestError) as caught:
                groq_client.generate_completion(self.messages)

        self.assertEqual(create_mock.call_count, 1)
        self.assertEqual(caught.exception.category, "authentication_error")
        sleep_mock.assert_not_called()
        print("PASS: 401 stopped immediately")

    @patch("pipeline.groq_client.time.sleep")
    def test_all_503_attempts_exhausted(self, sleep_mock):
        server_error = make_status_error(503, "Service unavailable")

        with patch.object(
            groq_client.client.chat.completions,
            "create",
            side_effect=server_error,
        ) as create_mock:
            with self.assertRaises(groq_client.GroqRequestError) as caught:
                groq_client.generate_completion(self.messages)

        self.assertEqual(create_mock.call_count, groq_client.MAX_ATTEMPTS)
        self.assertEqual(caught.exception.attempts, groq_client.MAX_ATTEMPTS)
        self.assertEqual(
            caught.exception.retry_count,
            groq_client.MAX_ATTEMPTS - 1,
        )
        print("PASS: exhausted attempts returned a controlled error")


if __name__ == "__main__":
    unittest.main(verbosity=2)