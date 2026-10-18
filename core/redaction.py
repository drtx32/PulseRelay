"""Redaction helpers for control-plane reads and delivery diagnostics."""
from __future__ import annotations

import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit


_URL_RE = re.compile(r"https?://[^\s\"'<>]+", re.IGNORECASE)
_BEARER_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/-]+=*")
_LONG_TOKEN_RE = re.compile(r"^[A-Za-z0-9_-]{24,}$")
_SENSITIVE_KEY_RE = re.compile(r"(?i)(authorization|cookie|password|passwd|secret|token|credential|api.?key|signature)")


def redact_url(value: str) -> str:
    """Hide URL credentials, query values and likely path-embedded webhook secrets."""
    try:
        parts = urlsplit(value)
        if not parts.scheme or not parts.hostname:
            return "<redacted-url>"
        host = parts.hostname
        if ":" in host and not host.startswith("["):
            host = f"[{host}]"
        if parts.port:
            host = f"{host}:{parts.port}"
        segments = parts.path.split("/")
        for index, segment in enumerate(segments):
            previous = segments[index - 1].lower() if index else ""
            if (previous in {"hook", "webhook", "token", "secret"}
                    or _LONG_TOKEN_RE.fullmatch(segment)):
                segments[index] = "<redacted>"
        query = urlencode([(key, "<redacted>") for key, _ in parse_qsl(parts.query, keep_blank_values=True)])
        return urlunsplit((parts.scheme, host, "/".join(segments), query, ""))
    except (TypeError, ValueError):
        return "<redacted-url>"


def redact_sensitive_text(value: str) -> str:
    text = _BEARER_RE.sub("Bearer <redacted>", value)
    return _URL_RE.sub(lambda match: redact_url(match.group(0).rstrip(".,);]")), text)


def redact_mapping(value):
    if isinstance(value, dict):
        result = {}
        for key, item in value.items():
            if _SENSITIVE_KEY_RE.search(str(key)) and not (
                isinstance(item, str) and item.startswith(("${secret:", "${env:"))
            ):
                result[key] = "<redacted>"
            elif isinstance(item, str) and (str(key).lower() == "url" or str(key).lower().endswith("_url")):
                result[key] = redact_url(item)
            else:
                result[key] = redact_mapping(item)
        return result
    if isinstance(value, list):
        return [redact_mapping(item) for item in value]
    if isinstance(value, str):
        return redact_sensitive_text(value)
    return value
