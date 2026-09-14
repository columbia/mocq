"""
Best-effort fetch of CodeQL's public per-language data-flow docs, to give the extraction LLM
call some prose alongside the raw source scan. Network access is optional: any failure
(offline, docs site down, changed URL) returns "" and extraction proceeds on the source scan
alone — this is supplementary material, not a required input (mirrors joern_docs_fetch.py).
"""

from __future__ import annotations

from ._html import strip_html

_LANG_DOC_SLUG = {
    "python": "analyzing-data-flow-in-python",
    "javascript": "analyzing-data-flow-in-javascript-and-typescript",
    "typescript": "analyzing-data-flow-in-javascript-and-typescript",
    "java": "analyzing-data-flow-in-java-and-kotlin",
}

_COMMON_URLS = [
    "https://codeql.github.com/docs/codeql-language-guides/introducing-data-flow-analysis/",
]


def fetch(language: str, timeout: int = 20) -> str:
    try:
        import requests
    except ImportError:
        return ""

    slug = _LANG_DOC_SLUG.get(language)
    urls = list(_COMMON_URLS)
    if slug:
        urls.append(f"https://codeql.github.com/docs/codeql-language-guides/{slug}/")

    blocks = []
    for url in urls:
        try:
            resp = requests.get(url, timeout=timeout, headers={"User-Agent": "mocq-dslgen/1.0"})
            if resp.status_code == 200 and resp.text:
                blocks.append(f"### {url}\n{strip_html(resp.text)}")
        except requests.RequestException:
            continue
    return "\n\n".join(blocks)
