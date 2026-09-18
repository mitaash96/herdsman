"""Secret redaction for anything leaving the process as text.

One function, one pattern table. Applied at the boundaries where agent output,
environment, or command lines become events, log lines, or CLI output — never
in the middle of the domain, where a redacted value would be mistaken for the
real one.
"""

import re

PLACEHOLDER = "[redacted]"

# Shapes, not names: a credential is recognizable by its own text, and a value
# that only a key-looking name identifies is still caught by the assignment rule.
_PATTERNS: tuple[re.Pattern[str], ...] = (
    # Provider-issued keys with a stable prefix.
    re.compile(r"\b(?:sk|pk|rk|ghp|gho|ghu|ghs|ghr|xox[abprs])[-_][A-Za-z0-9_-]{16,}"),
    # Bearer/authorization headers.
    re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"),
    # key=value / "key": "value" where the name says secret.
    re.compile(
        r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*)(\"?\s*[:=]\s*\"?)([^\s\"',}]{4,})"
    ),
    # Private key blocks.
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----", re.S),
)


def redact(text: str) -> str:
    """Replace credential-shaped substrings with `PLACEHOLDER`.

    Redaction is one-way and lossy by design: the caller keeps no map back.
    """
    if not text:
        return text
    result = text
    for pattern in _PATTERNS:
        if pattern.groups == 3:
            result = pattern.sub(rf"\1\2{PLACEHOLDER}", result)
        else:
            result = pattern.sub(PLACEHOLDER, result)
    return result


__all__ = ["PLACEHOLDER", "redact"]
