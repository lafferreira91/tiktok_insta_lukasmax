"""One TLS context for every outbound HTTPS call.

Python's ``urllib`` builds its default context from OpenSSL's compiled-in CA
path, which on macOS points at a store the system does not populate. Every HTTPS
request then fails with ``CERTIFICATE_VERIFY_FAILED`` -- locally only, since the
Linux runner finds its system store. That asymmetry is the trap: the Graph API
calls would work in CI and fail on the Mac, or the reverse after an OS upgrade.

Uses ``certifi``'s bundle when present and falls back to the default context
otherwise. Verification is never disabled: these calls carry an access token.
"""

from __future__ import annotations

import ssl
from functools import lru_cache


@lru_cache(maxsize=1)
def ssl_context() -> ssl.SSLContext:
    try:
        import certifi
    except ImportError:
        return ssl.create_default_context()
    return ssl.create_default_context(cafile=certifi.where())
