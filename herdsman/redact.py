"""Secret redaction for anything leaving the process as text.

One function, one pattern table. Applied at the boundaries where agent output,
environment, or command lines become events, log lines, or CLI output — never
in the middle of the domain, where a redacted value would be mistaken for the
real one.
"""

import re
from typing import cast

PLACEHOLDER = "[redacted]"

_SECRET_NAME = re.compile(
    r"(?i)(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)"
)
_SECRET_OPTION = re.compile(
    r"(?i)--?(?:api[-_]?key|secret|token|password|passwd|credential)"
)

# Shapes, not names: a credential is recognizable by its own text, and a value
# that only a key-looking name identifies is still caught by the assignment rule.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    # Provider-issued keys with a stable prefix.
    (
        re.compile(
            r"\b(?:sk|pk|rk|npm|ghp|gho|ghu|ghs|ghr|xox[abprs])[-_][A-Za-z0-9_-]{16,}"
            + r"|\bAKIA[A-Z0-9]{16}\b|\bAIza[A-Za-z0-9_-]{30,}"
        ),
        PLACEHOLDER,
    ),
    # Bearer/authorization headers.
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{12,}"), PLACEHOLDER),
    # key=value / "key": "value" where the name says secret.
    (
        re.compile(
            r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|SECRET|TOKEN|PASSWORD|PASSWD|CREDENTIAL)[A-Z0-9_]*)(\"?\s*[:=]\s*\"?)([^\s\"',}]{4,})"
        ),
        rf"\1\2{PLACEHOLDER}",
    ),
    # Shell command options using either --token=value or --token value.
    (
        re.compile(
            r"(?i)(--?(?:api[-_]?key|secret|token|password|passwd|credential))(\s+|=\s*)([\"']?)([^\s\"']{4,})"
        ),
        rf"\1\2\3{PLACEHOLDER}",
    ),
    # Private key blocks.
    (
        re.compile(
            r"-----BEGIN [A-Z ]*PRIVATE KEY-----.*?-----END [A-Z ]*PRIVATE KEY-----",
            re.S,
        ),
        PLACEHOLDER,
    ),
)


def contains_credential(text: str) -> bool:
    """Whether `text` holds anything credential-shaped.

    Used where redacting would destroy the artifact — a patch rewritten to hide
    a secret no longer applies — so the finding is reported for review instead.
    """
    return any(pattern.search(text) for pattern, _ in _PATTERNS)


def redact(text: str) -> str:
    """Replace credential-shaped substrings with `PLACEHOLDER`.

    Redaction is one-way and lossy by design: the caller keeps no map back.
    """
    if not text:
        return text
    result = text
    for pattern, replacement in _PATTERNS:
        result = pattern.sub(replacement, result)
    return result


def redact_value(value: object) -> object:
    """Redact nested JSON-like payloads without changing their shape."""
    if isinstance(value, str):
        return redact(value)
    if isinstance(value, dict):
        data = cast(dict[object, object], value)
        return {
            key: (
                PLACEHOLDER
                if isinstance(key, str)
                and _SECRET_NAME.search(key)
                and isinstance(item, str)
                else redact_value(item)
            )
            for key, item in data.items()
        }
    if isinstance(value, list):
        redacted: list[object] = []
        secret_value = False
        for item in cast(list[object], value):
            redacted.append(
                PLACEHOLDER if secret_value and isinstance(item, str) else redact_value(item)
            )
            secret_value = isinstance(item, str) and _SECRET_OPTION.fullmatch(item.strip()) is not None
        return redacted
    if isinstance(value, tuple):
        return tuple(redact_value(item) for item in cast(tuple[object, ...], value))
    return value


__all__ = ["PLACEHOLDER", "redact", "redact_value"]
