from __future__ import annotations

from collections import deque
from dataclasses import dataclass
import threading
import time
from urllib.parse import urlparse


LOC_JSON_API_REQUESTS_PER_MINUTE = 20
LOC_JSON_API_BLOCK_PENALTY_SECONDS = 60 * 60
LOC_MICROSERVICE_REQUESTS_PER_MINUTE = 150
LOC_STREAMING_MEDIA_REQUESTS_PER_MINUTE_MIN = 60
LOC_STREAMING_MEDIA_REQUESTS_PER_MINUTE_MAX = 150
LOC_BULK_REQUESTS_PER_WINDOW = 10
LOC_BULK_WINDOW_SECONDS = 10 * 60

LOC_JSON_API_MIN_INTERVAL_SECONDS = 60 / LOC_JSON_API_REQUESTS_PER_MINUTE
LOC_MICROSERVICE_MIN_INTERVAL_SECONDS = 60 / LOC_MICROSERVICE_REQUESTS_PER_MINUTE
LOC_STREAMING_MEDIA_MIN_INTERVAL_SECONDS = (
    60 / LOC_STREAMING_MEDIA_REQUESTS_PER_MINUTE_MIN
)
LOC_BULK_MIN_INTERVAL_SECONDS = (
    LOC_BULK_WINDOW_SECONDS / LOC_BULK_REQUESTS_PER_WINDOW
)

RATE_LIMIT_SAFETY_MARGIN_SECONDS = 0.05
LOC_BULK_SAFETY_MARGIN_SECONDS = 1.0
LOC_JSON_API_RESERVED_REQUESTS_PER_WINDOW = 2
LOC_JSON_API_SAFETY_MARGIN_SECONDS = 0.25


@dataclass(frozen=True, slots=True)
class RateLimit:
    name: str
    requests: int
    window_seconds: float
    safety_margin_seconds: float = RATE_LIMIT_SAFETY_MARGIN_SECONDS
    reserved_requests: int = 0

    @property
    def paced_requests(self) -> int:
        return max(1, self.requests - self.reserved_requests)

    @property
    def min_interval_seconds(self) -> float:
        return self.window_seconds / self.paced_requests + self.safety_margin_seconds

    @property
    def retry_cooldown_seconds(self) -> float:
        return self.window_seconds + self.safety_margin_seconds


LOC_JSON_API_RATE_LIMIT = RateLimit(
    name="LOC JSON/YAML search and data API",
    requests=LOC_JSON_API_REQUESTS_PER_MINUTE,
    window_seconds=60,
    safety_margin_seconds=LOC_JSON_API_SAFETY_MARGIN_SECONDS,
    reserved_requests=LOC_JSON_API_RESERVED_REQUESTS_PER_WINDOW,
)
LOC_MICROSERVICE_RATE_LIMIT = RateLimit(
    name="LOC text/image/media microservices",
    requests=LOC_MICROSERVICE_REQUESTS_PER_MINUTE,
    window_seconds=60,
)
LOC_STREAMING_MEDIA_RATE_LIMIT = RateLimit(
    name="LOC streaming and media services",
    requests=LOC_STREAMING_MEDIA_REQUESTS_PER_MINUTE_MIN,
    window_seconds=60,
)
LOC_BULK_OCR_RATE_LIMIT = RateLimit(
    name="LOC bulk data and OCR",
    requests=LOC_BULK_REQUESTS_PER_WINDOW,
    window_seconds=LOC_BULK_WINDOW_SECONDS,
    safety_margin_seconds=LOC_BULK_SAFETY_MARGIN_SECONDS,
)


class RateLimiter:
    """Thread-safe sliding-window limiter for request starts."""

    def __init__(self, limit: RateLimit) -> None:
        self.limit = limit
        self._lock = threading.Lock()
        self._next_request_at = 0.0
        self._deferred_until = 0.0
        self._request_times: deque[float] = deque()

    def wait(self) -> None:
        while True:
            delay = self._delay_until_next_request()
            if delay <= 0:
                return
            time.sleep(delay)

    def defer(self, seconds: float) -> None:
        if seconds <= 0:
            return
        with self._lock:
            self._deferred_until = max(
                self._deferred_until,
                time.monotonic() + seconds,
            )

    def _delay_until_next_request(self) -> float:
        with self._lock:
            now = time.monotonic()
            self._drop_expired_request_times(now)

            wait_until = max(self._next_request_at, self._deferred_until)
            if len(self._request_times) >= self.limit.paced_requests:
                wait_until = max(
                    wait_until,
                    self._request_times[0]
                    + self.limit.window_seconds
                    + self.limit.safety_margin_seconds,
                )

            if now < wait_until:
                return wait_until - now

            self._request_times.append(now)
            self._next_request_at = now + self.limit.min_interval_seconds
            return 0.0

    def _drop_expired_request_times(self, now: float) -> None:
        expires_before = now - self.limit.window_seconds
        while self._request_times and self._request_times[0] <= expires_before:
            self._request_times.popleft()


LOC_JSON_API_LIMITER = RateLimiter(LOC_JSON_API_RATE_LIMIT)
LOC_MICROSERVICE_LIMITER = RateLimiter(LOC_MICROSERVICE_RATE_LIMIT)
LOC_STREAMING_MEDIA_LIMITER = RateLimiter(LOC_STREAMING_MEDIA_RATE_LIMIT)
LOC_BULK_OCR_LIMITER = RateLimiter(LOC_BULK_OCR_RATE_LIMIT)


def is_remote_url(url: str) -> bool:
    return urlparse(url).scheme in {"http", "https"}


def is_bulk_ocr_url(url: str) -> bool:
    parsed = urlparse(url)
    return (
        parsed.scheme in {"http", "https"}
        and parsed.netloc == "chroniclingamerica.loc.gov"
        and parsed.path.startswith("/data/ocr/")
    )
