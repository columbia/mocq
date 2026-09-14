"""Vendored copy of cpgqls-client (https://github.com/joernio/cpgqls-client-python).

Trimmed to the two modules JoernEngine needs. Patched so CPGQLSClient owns a
private event loop instead of asyncio.get_event_loop().
"""

from .client import CPGQLSClient, CPGQLSTransport  # noqa: F401
from .queries import import_code_query  # noqa: F401

__all__ = ["CPGQLSClient", "CPGQLSTransport", "import_code_query"]
