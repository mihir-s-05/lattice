from __future__ import annotations

from typing import Any, Mapping, MutableMapping, Sequence


SENSITIVE_KEYS = {
    "api_key",
    "authorization",
    "x-api-key",
    "x-api-token",
    "access_token",
    "token",
    "client_secret",
    "secret",
    "password",
}


def _is_sensitive_key(key: str) -> bool:
    k = key.lower()
    if k in SENSITIVE_KEYS:
        return True
    return any(t in k for t in ["api_key", "apikey", "token", "secret", "password"])


def redact_secrets(obj: Any) -> Any:
    """Recursively redact sensitive values in mappings/lists.

    - For dict keys that look sensitive, replace values with "REDACTED".
    - For lists/tuples, redact each element.
    - For other types, return as-is.
    """
    if isinstance(obj, Mapping):
        out: MutableMapping[str, Any] = {}
        for k, v in obj.items():
            if _is_sensitive_key(str(k)):
                out[k] = "REDACTED"
            else:
                out[k] = redact_secrets(v)
        return dict(out)
    if isinstance(obj, Sequence) and not isinstance(obj, (str, bytes, bytearray)):
        return [redact_secrets(x) for x in obj]
    return obj

