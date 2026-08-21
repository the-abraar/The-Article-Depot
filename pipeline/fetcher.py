"""A crawler that is safe to leave running unattended.

Four behaviours, in the order a site owner would care about:

1. robots.txt is fetched once per host and obeyed. Fetching robots.txt and
   then ignoring it is a worse signal than never reading it, so Crawl-delay
   is honoured too -- and it can only slow us down, never speed us up past
   the configured floor.
2. Requests to a host are spaced by that delay, enforced by a per-host clock.
3. We identify with a real, contactable User-Agent.
4. 429/403 is treated as "back off", not "retry harder": exponential backoff
   honouring Retry-After, and after `host_block_threshold` consecutive blocks
   the host is dropped for the rest of the run.
"""

from __future__ import annotations

import logging
import time
import urllib.error
import urllib.request
from dataclasses import dataclass, field
from typing import Callable
from urllib.parse import urlparse
from urllib.robotparser import RobotFileParser

from .config import Config

log = logging.getLogger(__name__)


class Blocked(Exception):
    """Host told us to go away, or robots.txt disallows the path."""


@dataclass
class FetchResult:
    url: str
    status: int
    body: bytes
    content_type: str = ""

    @property
    def text(self) -> str:
        return self.body.decode("utf-8", errors="replace")


@dataclass
class _HostState:
    rules: RobotFileParser | None = None
    robots_loaded: bool = False
    crawl_delay: float = 0.0
    last_request: float | None = None
    consecutive_blocks: int = 0
    dropped: bool = False


@dataclass
class PoliteFetcher:
    cfg: Config
    opener: Callable[[urllib.request.Request, float], object] | None = None
    sleep: Callable[[float], None] = time.sleep
    clock: Callable[[], float] = time.monotonic
    hosts: dict[str, _HostState] = field(default_factory=dict)

    # --- plumbing ----------------------------------------------------------

    def _open(self, url: str, timeout: float):
        req = urllib.request.Request(url, headers={
            "User-Agent": self.cfg.user_agent,
            "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        })
        if self.opener is not None:            # injected in tests
            return self.opener(req, timeout)
        return urllib.request.urlopen(req, timeout=timeout)

    def _state(self, host: str) -> _HostState:
        return self.hosts.setdefault(host, _HostState())

    # --- robots ------------------------------------------------------------

    def _load_robots(self, scheme: str, host: str) -> None:
        st = self._state(host)
        if st.robots_loaded:
            return
        st.robots_loaded = True                # one attempt per host per run
        parser = RobotFileParser()
        url = f"{scheme}://{host}/robots.txt"
        try:
            resp = self._open(url, self.cfg.request_timeout)
            body = resp.read(self.cfg.max_source_bytes)
            parser.parse(body.decode("utf-8", errors="replace").splitlines())
            st.rules = parser
        except Exception as exc:
            # No robots.txt is permission to crawl politely, not permission
            # to crawl fast -- the configured floor still applies below.
            log.debug("no robots.txt for %s (%s)", host, exc)
            st.rules = None

        delay = None
        if st.rules is not None:
            try:
                delay = st.rules.crawl_delay(self.cfg.user_agent)
            except Exception:
                delay = None
        # Crawl-delay may raise our floor, never lower it.
        st.crawl_delay = max(float(delay or 0.0), self.cfg.default_crawl_delay)

    def allowed(self, url: str) -> bool:
        parts = urlparse(url)
        if parts.scheme not in ("http", "https") or not parts.netloc:
            return False
        self._load_robots(parts.scheme, parts.netloc)
        st = self._state(parts.netloc)
        if st.dropped:
            return False
        if st.rules is None:
            return True
        return st.rules.can_fetch(self.cfg.user_agent, url)

    # --- pacing ------------------------------------------------------------

    def _wait_turn(self, host: str) -> None:
        st = self._state(host)
        # `None`, not 0.0 -- a monotonic clock may legitimately read zero, and
        # treating that as "never fetched" would skip the very first delay.
        if st.last_request is not None:
            elapsed = self.clock() - st.last_request
            remaining = st.crawl_delay - elapsed
            if remaining > 0:
                self.sleep(remaining)
        st.last_request = self.clock()

    # --- the one public entry point ----------------------------------------

    def get(self, url: str) -> FetchResult:
        parts = urlparse(url)
        host = parts.netloc
        if not self.allowed(url):
            raise Blocked(f"robots.txt disallows {url}")

        st = self._state(host)
        backoff = 1.0
        last_exc: Exception | None = None

        for attempt in range(self.cfg.max_retries):
            if st.dropped:
                raise Blocked(f"host {host} dropped after repeated blocks")
            self._wait_turn(host)
            try:
                resp = self._open(url, self.cfg.request_timeout)
                body = resp.read(self.cfg.max_source_bytes)
                status = getattr(resp, "status", 200) or 200
                ctype = ""
                try:
                    ctype = resp.headers.get("Content-Type", "")
                except Exception:
                    pass
                st.consecutive_blocks = 0
                return FetchResult(url=url, status=status, body=body,
                                   content_type=ctype)
            except urllib.error.HTTPError as exc:
                last_exc = exc
                if exc.code in (429, 403):
                    st.consecutive_blocks += 1
                    if st.consecutive_blocks >= self.cfg.host_block_threshold:
                        st.dropped = True
                        log.warning("dropping %s after %d blocks",
                                    host, st.consecutive_blocks)
                        raise Blocked(f"host {host} returned {exc.code} repeatedly")
                    wait = self._retry_after(exc) or backoff
                    log.info("%s returned %d; backing off %.1fs", host, exc.code, wait)
                    self.sleep(wait)
                    backoff *= 2
                    continue
                if 500 <= exc.code < 600:
                    self.sleep(backoff)
                    backoff *= 2
                    continue
                raise                              # 404 and friends: don't retry
            except Exception as exc:               # transport-level
                last_exc = exc
                self.sleep(backoff)
                backoff *= 2

        raise Blocked(f"giving up on {url}: {last_exc}")

    @staticmethod
    def _retry_after(exc: urllib.error.HTTPError) -> float | None:
        try:
            value = exc.headers.get("Retry-After")
        except Exception:
            return None
        if not value:
            return None
        try:
            return float(value)
        except ValueError:
            return None
