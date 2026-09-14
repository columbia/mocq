"""
Best-effort fetch of Joern's public CPGQL reference docs, to give the extraction LLM
call some prose alongside the raw source scan. Network access is optional: any failure
(offline, docs site down, changed URL) returns "" and extraction proceeds on the source
scan alone — this is supplementary material, not a required input.
"""

from __future__ import annotations

from typing import List

from ._html import strip_html

_DOC_URLS = [
    "https://docs.joern.io/cpgql/reference-card/",
    "https://docs.joern.io/traversal-basics/",
]


def fetch(urls: List[str] = _DOC_URLS, timeout: int = 20) -> str:
    """Fetch and concatenate the plain-text content of `urls`. Best-effort: "" on total failure."""
    try:
        import requests
    except ImportError:
        return ""

    blocks = []
    for url in urls:
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": "mocq-dslgen/1.0"})
            if resp.status_code == 200 and resp.text:
                blocks.append(f"### {url}\n{strip_html(resp.text)}")
        except requests.RequestException:
            continue
    return "\n\n".join(blocks)
