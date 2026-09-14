"""Tiny shared HTML-to-text helper for the docs fetchers (joern_docs_fetch, codeql_docs_fetch)."""

from __future__ import annotations

import re

_TAG_RE = re.compile(r"<script.*?</script>|<style.*?</style>|<[^>]+>", re.DOTALL | re.IGNORECASE)
_WS_RE = re.compile(r"[ \t]+")
_BLANKLINES_RE = re.compile(r"\n{3,}")


def strip_html(html: str) -> str:
    text = _TAG_RE.sub(" ", html)
    text = re.sub(r"&amp;", "&", text)
    text = re.sub(r"&lt;", "<", text)
    text = re.sub(r"&gt;", ">", text)
    text = re.sub(r"&quot;|&#39;", '"', text)
    text = _WS_RE.sub(" ", text)
    return _BLANKLINES_RE.sub("\n\n", text).strip()
