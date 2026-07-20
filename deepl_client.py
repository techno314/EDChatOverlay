"""Minimal DeepL REST client for translating incoming chat text."""

import requests

_FREE_ENDPOINT = "https://api-free.deepl.com/v2/translate"
_PRO_ENDPOINT = "https://api.deepl.com/v2/translate"
_TIMEOUT_SEC = 6


class DeepLError(Exception):
    """Raised when a DeepL API call fails or returns an unexpected response."""


def _endpoint_for_key(api_key: str) -> str:
    # DeepL's free-tier keys always end in ":fx" and only work against the
    # free API host; Pro keys use the standard host.
    return _FREE_ENDPOINT if api_key.strip().endswith(":fx") else _PRO_ENDPOINT


def translate(api_key: str, text: str, target_lang: str) -> str:
    """Translate `text` to `target_lang`. Raises DeepLError on failure."""
    if not api_key or not text.strip():
        raise DeepLError("Missing API key or empty text")

    url = _endpoint_for_key(api_key)
    try:
        resp = requests.post(
            url,
            headers={"Authorization": f"DeepL-Auth-Key {api_key}"},
            data={
                "text": text,
                "target_lang": target_lang,
            },
            timeout=_TIMEOUT_SEC,
        )
    except requests.RequestException as exc:
        raise DeepLError(f"Network error contacting DeepL: {exc}") from exc

    if resp.status_code != 200:
        raise DeepLError(f"DeepL API returned HTTP {resp.status_code}: {resp.text[:200]}")

    try:
        payload = resp.json()
        return payload["translations"][0]["text"]
    except (ValueError, KeyError, IndexError) as exc:
        raise DeepLError(f"Unexpected DeepL response: {exc}") from exc
