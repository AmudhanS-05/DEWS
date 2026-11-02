"""Small HTTP helper with retries and a polite User-Agent."""
from __future__ import annotations

import time

import requests

from common import config


def get_json(url: str, retries: int = 3, backoff_s: float = 5.0) -> dict:
    last = None
    for attempt in range(1, retries + 1):
        try:
            r = requests.get(url, timeout=config.HTTP_TIMEOUT_S,
                             headers={"User-Agent": config.USER_AGENT, "Accept": "application/json"})
            if r.status_code == 404:
                raise FileNotFoundError(f"404 for {url}")
            r.raise_for_status()
            return r.json()
        except FileNotFoundError:
            raise
        except Exception as e:  # network errors, 5xx, bad JSON
            last = e
            if attempt < retries:
                time.sleep(backoff_s * attempt)
    raise RuntimeError(f"GET failed after {retries} attempts: {url}: {last}")
