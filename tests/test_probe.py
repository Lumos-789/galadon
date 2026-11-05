from __future__ import annotations

import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from galadon.adapters.probe import ProbeNotReady, fetch


class FakeResponse:
    def __init__(self, body: bytes, status: int = 200) -> None:
        self._body = body
        self._status = status

    def getcode(self) -> int:
        return self._status

    def read(self, _size: int = -1) -> bytes:
        return self._body

    def __enter__(self) -> FakeResponse:
        return self

    def __exit__(self, *_exc: object) -> bool:
        return False


class ProbeReadinessTests(unittest.TestCase):
    def test_a_refused_connection_is_retried_then_succeeds(self) -> None:
        attempts = {"n": 0}
        sleeps: list[float] = []

        def fake_urlopen(url: str, timeout: float | None = None) -> FakeResponse:
            attempts["n"] += 1
            if attempts["n"] < 3:
                raise ConnectionResetError(54, "Connection reset by peer")
            return FakeResponse(b'{"commit":"abc1234"}')

        with mock.patch("galadon.adapters.probe.urllib.request.urlopen", fake_urlopen):
            status, body = fetch(
                "http://127.0.0.1:1/health.json",
                ready_seconds=5.0,
                interval=0.25,
                sleep=sleeps.append,
            )

        self.assertEqual(status, 200)
        self.assertIn("abc1234", body)
        self.assertEqual(attempts["n"], 3)
        self.assertEqual(sleeps, [0.25, 0.25])

    def test_the_readiness_budget_is_finite(self) -> None:
        with mock.patch(
            "galadon.adapters.probe.urllib.request.urlopen",
            side_effect=ConnectionResetError(54, "Connection reset by peer"),
        ):
            with self.assertRaises(ProbeNotReady) as caught:
                fetch(
                    "http://127.0.0.1:1/health.json",
                    ready_seconds=0.0,
                    interval=0.0,
                    sleep=lambda _seconds: None,
                )

        message = str(caught.exception)
        self.assertIn("no answer within", message)
        self.assertIn("attempt(s)", message)
        self.assertIsInstance(caught.exception.last, ConnectionResetError)
        self.assertEqual(caught.exception.budget, 0.0)

    def test_an_http_answer_is_final_and_never_retried(self) -> None:
        attempts = {"n": 0}

        def fake_urlopen(url: str, timeout: float | None = None) -> FakeResponse:
            attempts["n"] += 1
            raise urllib.error.HTTPError(url, 404, "Not Found", {}, None)

        def refuse_sleep(_seconds: float) -> None:
            raise AssertionError("an HTTP answer must not be retried")

        with mock.patch("galadon.adapters.probe.urllib.request.urlopen", fake_urlopen):
            status, body = fetch(
                "http://127.0.0.1:1/health.json",
                ready_seconds=30.0,
                interval=0.25,
                sleep=refuse_sleep,
            )

        self.assertEqual(status, 404)
        self.assertEqual(body, "")
        self.assertEqual(attempts["n"], 1)


if __name__ == "__main__":
    unittest.main()
