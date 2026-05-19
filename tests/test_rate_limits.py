from __future__ import annotations

import pytest

from civil_war_search import rate_limits
from civil_war_search.rate_limits import (
    LOC_BULK_REQUESTS_PER_WINDOW,
    LOC_BULK_WINDOW_SECONDS,
    LOC_JSON_API_BLOCK_PENALTY_SECONDS,
    LOC_JSON_API_RATE_LIMIT,
    LOC_JSON_API_REQUESTS_PER_MINUTE,
    LOC_MICROSERVICE_REQUESTS_PER_MINUTE,
    RateLimit,
    RateLimiter,
    is_bulk_ocr_url,
    is_remote_url,
)


def test_loc_rate_limit_constants_match_documented_caps() -> None:
    assert LOC_JSON_API_REQUESTS_PER_MINUTE == 20
    assert LOC_JSON_API_BLOCK_PENALTY_SECONDS == 3600
    assert LOC_JSON_API_RATE_LIMIT.paced_requests == 18
    assert LOC_MICROSERVICE_REQUESTS_PER_MINUTE == 150
    assert LOC_BULK_REQUESTS_PER_WINDOW == 10
    assert LOC_BULK_WINDOW_SECONDS == 600


def test_url_classification_limits_only_remote_bulk_ocr_urls() -> None:
    assert is_remote_url("https://www.loc.gov/item/sn83045462/?fo=json")
    assert not is_remote_url("file:///tmp/page.json")
    assert is_bulk_ocr_url(
        "https://chroniclingamerica.loc.gov/data/ocr/dlc_test_ver01.tar.bz2"
    )
    assert not is_bulk_ocr_url("https://www.loc.gov/item/sn83045462/?fo=json")
    assert not is_bulk_ocr_url("file:///tmp/dlc_test_ver01.tar.bz2")


def test_rate_limiter_spaces_request_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = [100.0]
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return current_time[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        current_time[0] += seconds

    monkeypatch.setattr(rate_limits.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(rate_limits.time, "sleep", fake_sleep)

    limiter = RateLimiter(
        RateLimit(
            name="test",
            requests=2,
            window_seconds=1,
            safety_margin_seconds=0,
        )
    )

    limiter.wait()
    limiter.wait()

    assert sleeps == pytest.approx([0.5])


def test_rate_limiter_enforces_sliding_window(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = [100.0]
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return current_time[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        current_time[0] += seconds

    monkeypatch.setattr(rate_limits.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(rate_limits.time, "sleep", fake_sleep)

    limiter = RateLimiter(
        RateLimit(
            name="test",
            requests=2,
            window_seconds=1,
            safety_margin_seconds=0,
        )
    )

    limiter.wait()
    limiter.wait()
    limiter.wait()

    assert sleeps == pytest.approx([0.5, 0.5])


def test_rate_limiter_defer_delays_all_callers(monkeypatch: pytest.MonkeyPatch) -> None:
    current_time = [100.0]
    sleeps: list[float] = []

    def fake_monotonic() -> float:
        return current_time[0]

    def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)
        current_time[0] += seconds

    monkeypatch.setattr(rate_limits.time, "monotonic", fake_monotonic)
    monkeypatch.setattr(rate_limits.time, "sleep", fake_sleep)

    limiter = RateLimiter(
        RateLimit(
            name="test",
            requests=10,
            window_seconds=1,
            safety_margin_seconds=0,
        )
    )

    limiter.wait()
    limiter.defer(5)
    limiter.wait()

    assert sleeps == pytest.approx([5])
