"""Shared HTTP layer for LSP adapters: rate limiting, retry, circuit breaker,
and block detection.

The block detector exists because of a reproduced incident: DTDC's public site
answers ~5 requests then serves a fake `HTTP 500` carrying a `Blocked!`
deception page. Retrying into that is how an egress IP gets banned -- and this
process shares egress with the Uniware pipeline. So a block opens the breaker
immediately and is never retried.

Brotli matters here too: Shadowfax serves `Content-Encoding: br`, and without
the `brotli` package installed `requests` hands back binary garbage that looks
like an empty page rather than an error.
"""
from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass, field
from typing import Any

import requests

DEFAULT_UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
              "(KHTML, like Gecko) Chrome/128.0.0.0 Safari/537.36")

TRANSIENT_STATUS = frozenset({429, 500, 502, 503, 504, 408})

#: Signatures that mean "blocked", independent of the HTTP status code.
GENERIC_BLOCK_SIGNATURES = ("Blocked!", "bot_deception", "captcha", "Captcha",
                            "Access Denied", "Are you a robot")


class BlockedError(RuntimeError):
    """The endpoint served a block/challenge page. Never retry this."""


class CircuitOpen(RuntimeError):
    """This adapter is circuit-broken for now; remaining AWBs are deferred."""


@dataclass
class _Breaker:
    """Per-adapter circuit breaker.

    Opens on sustained failure so one dead LSP cannot consume the whole run
    budget, and instantly on a block signature.
    """

    consecutive_failures: int = 0
    window: list[bool] = field(default_factory=list)   # True = error
    opened_until: float = 0.0
    open_count: int = 0
    reason: str | None = None

    max_consecutive: int = 10
    window_size: int = 50
    error_rate_limit: float = 0.30
    cooldown_s: float = 1800.0     # 30 min

    def is_open(self) -> bool:
        return time.time() < self.opened_until

    def trip(self, reason: str, cooldown: float | None = None) -> None:
        self.opened_until = time.time() + (cooldown or self.cooldown_s)
        self.open_count += 1
        self.reason = reason

    def record(self, error: bool) -> None:
        self.window.append(error)
        if len(self.window) > self.window_size:
            self.window.pop(0)
        if error:
            self.consecutive_failures += 1
        else:
            self.consecutive_failures = 0
        if self.consecutive_failures >= self.max_consecutive:
            self.trip(f"{self.consecutive_failures} consecutive failures")
        elif len(self.window) >= self.window_size:
            rate = sum(self.window) / len(self.window)
            if rate > self.error_rate_limit:
                self.trip(f"error rate {rate:.0%} over {self.window_size} calls")


@dataclass
class _Limiter:
    """Minimum-interval limiter, one per adapter, thread-safe."""

    min_interval_s: float
    _last: float = 0.0
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def wait(self) -> None:
        with self._lock:
            gap = time.time() - self._last
            if gap < self.min_interval_s:
                time.sleep(self.min_interval_s - gap)
            self._last = time.time()


class HttpClient:
    """One instance per run, shared by every adapter.

    Keeps per-adapter limiters and breakers, and a global semaphore so a whole
    run never looks like a burst from a single IP.
    """

    def __init__(self, global_concurrency: int = 12,
                 user_agent: str = DEFAULT_UA) -> None:
        self._session = requests.Session()
        self._session.headers["User-Agent"] = user_agent
        self._sem = threading.Semaphore(global_concurrency)
        self._limiters: dict[str, _Limiter] = {}
        self._breakers: dict[str, _Breaker] = {}
        self._lock = threading.Lock()
        self.stats: dict[str, dict[str, Any]] = {}

    def breaker(self, adapter_id: str) -> _Breaker:
        with self._lock:
            return self._breakers.setdefault(adapter_id, _Breaker())

    def limiter(self, adapter_id: str, min_interval_s: float) -> _Limiter:
        with self._lock:
            return self._limiters.setdefault(adapter_id, _Limiter(min_interval_s))

    def _bump(self, adapter_id: str, key: str) -> None:
        with self._lock:
            self.stats.setdefault(adapter_id, {}).setdefault(key, 0)
            self.stats[adapter_id][key] += 1

    def request(self, adapter_id: str, method: str, url: str, *,
                min_interval_s: float = 0.5,
                timeout: tuple[float, float] = (10.0, 30.0),
                headers: dict[str, str] | None = None,
                block_signatures: tuple[str, ...] = (),
                retries: int = 3, **kwargs: Any) -> requests.Response:
        breaker = self.breaker(adapter_id)
        if breaker.is_open():
            raise CircuitOpen(f"{adapter_id} circuit open: {breaker.reason}")

        limiter = self.limiter(adapter_id, min_interval_s)
        sigs = tuple(block_signatures) + GENERIC_BLOCK_SIGNATURES
        last_exc: Exception | None = None

        # The breaker records ONE outcome per request, not per attempt.
        # Counting attempts made it far more trigger-happy than its own
        # thresholds imply: four failing AWBs retried three times each produced
        # twelve consecutive failure records and opened a breaker calibrated to
        # ten, aborting Shadowfax after 29 of 5,167 calls on the first
        # full-scale run. Retries are one request's worth of trouble.
        for attempt in range(1, retries + 1):
            limiter.wait()
            with self._sem:
                try:
                    r = self._session.request(method, url, timeout=timeout,
                                              headers=headers or {}, **kwargs)
                except Exception as exc:
                    last_exc = exc
                    self._bump(adapter_id, "transport_error")
                    if attempt < retries:
                        self._backoff(attempt)
                        continue
                    breaker.record(True)          # request exhausted its retries
                    if breaker.is_open():
                        raise CircuitOpen(f"{adapter_id}: {breaker.reason}") from exc
                    raise

            head = (r.text or "")[:5000]
            if any(s in head for s in sigs):
                # Never retry a block, and open the breaker at once.
                self._bump(adapter_id, "blocked")
                breaker.trip("block signature in response")
                raise BlockedError(
                    f"{adapter_id} served a block/challenge page (http={r.status_code})")

            if r.status_code in TRANSIENT_STATUS:
                self._bump(adapter_id, f"http_{r.status_code}")
                if attempt < retries:
                    self._backoff(attempt, r.headers.get("Retry-After"))
                    continue
                breaker.record(True)              # still transient after retries
                if breaker.is_open():
                    raise CircuitOpen(f"{adapter_id}: {breaker.reason}")
                return r

            # 401/403 and 4xx generally are answers, not failures to retry.
            breaker.record(False)
            self._bump(adapter_id, "ok" if r.ok else f"http_{r.status_code}")
            return r

        if last_exc:
            raise last_exc
        raise RuntimeError(f"{adapter_id}: exhausted retries for {url}")

    @staticmethod
    def _backoff(attempt: int, retry_after: str | None = None) -> None:
        if retry_after:
            try:
                time.sleep(min(float(retry_after), 30.0))
                return
            except (TypeError, ValueError):
                pass
        base = 0.8 * (2 ** (attempt - 1))
        time.sleep(base * random.uniform(0.7, 1.3))   # jitter, avoids lockstep

    def report(self) -> dict[str, Any]:
        with self._lock:
            return {
                "calls": {k: dict(v) for k, v in self.stats.items()},
                "breakers": {k: {"open": b.is_open(), "opens": b.open_count,
                                 "reason": b.reason}
                             for k, b in self._breakers.items()},
            }
