"""Thin HTTP clients for Serper (discovery) and Firecrawl (extraction).

Every response is cached to cache/<sha1>.json, so re-runs and crash recovery
cost zero API credits.
"""

import hashlib
import json
import os
import sys
import threading
import time
from pathlib import Path

import requests

# Windows consoles default to cp1252, which cannot print Azerbaijani text.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except (AttributeError, ValueError):
        pass

ROOT = Path(__file__).parent
CACHE = ROOT / "cache"

SERPER_URL = "https://google.serper.dev/search"
FIRECRAWL_BASE = "https://api.firecrawl.dev/v2"


def load_env():
    """Minimal .env parser - no python-dotenv dependency."""
    env_path = ROOT / ".env"
    if not env_path.exists():
        return
    for line in env_path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key, val = key.strip(), val.strip()
        # Tolerate quoted values, and the spaced form Google displays
        # app passwords in ("abcd efgh ijkl mnop").
        if len(val) >= 2 and val[0] == val[-1] and val[0] in "\"'":
            val = val[1:-1].strip()
        if key == "GMAIL_APP_PASSWORD":
            val = val.replace(" ", "")
        os.environ[key] = val


load_env()


def require_key(name):
    val = os.environ.get(name, "").strip()
    if not val:
        sys.exit(f"ERROR: {name} is not set. Add it to {ROOT / '.env'}")
    return val


def _cache_get(kind, payload):
    CACHE.mkdir(exist_ok=True)
    blob = json.dumps({"kind": kind, "payload": payload}, sort_keys=True)
    key = hashlib.sha1(blob.encode()).hexdigest()
    return CACHE / f"{kind}_{key}.json"


def _cached(kind, payload, fetch):
    """Return cached response if present, else call fetch() and store it."""
    path = _cache_get(kind, payload)
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            path.unlink()
    result = fetch()
    if result is not None:
        path.write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return result


class CreditsExhausted(RuntimeError):
    """Raised on 401/402 so callers stop instead of silently under-collecting."""


class RateLimited(RuntimeError):
    """429 - retryable after a wait, unlike CreditsExhausted."""


# Firecrawl's free tier allows 6 requests/minute. Serialise calls across threads
# so a burst never trips it.
_FC_LOCK = threading.Lock()
_FC_MIN_INTERVAL = 11.0  # seconds between Firecrawl calls (~5.4/min)
_fc_last = [0.0]


def _firecrawl_gate():
    with _FC_LOCK:
        wait = _FC_MIN_INTERVAL - (time.monotonic() - _fc_last[0])
        if wait > 0:
            time.sleep(wait)
        _fc_last[0] = time.monotonic()


def _check_fatal(resp, provider):
    if resp.status_code in (401, 402):
        raise CreditsExhausted(
            f"{provider} returned HTTP {resp.status_code}: {resp.text[:300]}"
        )
    if resp.status_code == 429:
        raise RateLimited(f"{provider} 429")


def serper_search(query, num=20, gl="az", hl="en"):
    """Google SERP via Serper. Returns the raw response dict, or None on failure."""
    payload = {"q": query, "num": num, "gl": gl, "hl": hl}

    def fetch():
        for attempt in range(3):
            try:
                r = requests.post(
                    SERPER_URL,
                    headers={
                        "X-API-KEY": require_key("SERPER_API_KEY"),
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=30,
                )
                _check_fatal(r, "Serper")
                if r.ok:
                    return r.json()
                print(f"  serper {r.status_code} for {query!r}", flush=True)
            except CreditsExhausted:
                raise
            except requests.RequestException as e:
                print(f"  serper error ({e}) attempt {attempt + 1}", flush=True)
            time.sleep(2 * (attempt + 1))
        return None

    return _cached("serper", payload, fetch)


def serper_organic(query, **kw):
    """Convenience: just the organic results list."""
    data = serper_search(query, **kw)
    return (data or {}).get("organic", []) or []


def firecrawl_scrape(url, formats=("markdown", "links"), wait_for=1500):
    """Scrape one URL. onlyMainContent=False because emails live in footers."""
    payload = {
        "url": url,
        "formats": list(formats),
        "onlyMainContent": False,
        "waitFor": wait_for,
        "timeout": 45000,
    }

    def fetch():
        for attempt in range(4):
            try:
                _firecrawl_gate()
                r = requests.post(
                    f"{FIRECRAWL_BASE}/scrape",
                    headers={
                        "Authorization": f"Bearer {require_key('FIRECRAWL_API_KEY')}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=120,
                )
                _check_fatal(r, "Firecrawl")
                if r.ok:
                    body = r.json()
                    if body.get("success"):
                        return body.get("data", {})
                    return None
                if r.status_code >= 500:
                    return None  # site itself is broken; don't burn retries
                print(f"  firecrawl {r.status_code} for {url}", flush=True)
                return None
            except CreditsExhausted:
                raise
            except RateLimited:
                time.sleep(20 * (attempt + 1))
            except requests.RequestException:
                time.sleep(5)
        return None

    return _cached("fc_scrape", payload, fetch)


def firecrawl_map(url, limit=200):
    """Enumerate a site's URLs cheaply, so contact/career pages are findable."""
    payload = {"url": url, "limit": limit}

    def fetch():
        for attempt in range(3):
            try:
                _firecrawl_gate()
                r = requests.post(
                    f"{FIRECRAWL_BASE}/map",
                    headers={
                        "Authorization": f"Bearer {require_key('FIRECRAWL_API_KEY')}",
                        "Content-Type": "application/json",
                    },
                    json=payload,
                    timeout=120,
                )
                _check_fatal(r, "Firecrawl")
                return r.json() if r.ok else None
            except CreditsExhausted:
                raise
            except RateLimited:
                time.sleep(20 * (attempt + 1))
            except requests.RequestException:
                time.sleep(5)
        return None

    return _cached("fc_map", payload, fetch)


def plain_fetch(url):
    """Last-resort direct fetch when Firecrawl itself errors on a URL."""
    headers = {
        "User-Agent": (
            "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
            "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
        )
    }
    for verify in (True, False):
        try:
            r = requests.get(url, headers=headers, timeout=20, verify=verify)
            if r.ok:
                return r.text
        except requests.RequestException:
            continue
    return None


if __name__ == "__main__":
    # Smoke test: prove both providers work, including the URL that 403'd.
    print("== Serper ==")
    hits = serper_organic("best IT companies in Baku Azerbaijan", num=10)
    print(f"organic results: {len(hits)}")
    for h in hits[:5]:
        print("  -", h.get("title"), "|", h.get("link"))

    print("\n== Firecrawl (the URL that returned 403 to plain fetching) ==")
    data = firecrawl_scrape("https://techbehemoths.com/companies/baku")
    md = (data or {}).get("markdown", "")
    print(f"markdown chars: {len(md)}")
    print(f"links found: {len((data or {}).get('links', []))}")
    print("--- first 600 chars ---")
    print(md[:600])
