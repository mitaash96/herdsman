"""Secret redaction for anything leaving the process as text.

One function, one pattern table. Applied at the boundaries where agent output,
environment, or command lines become events, log lines, or CLI output — never
in the middle of the domain, where a redacted value would be mistaken for the
real one.

A credential is recognized by the shape of its VALUE, not by a nearby word:
token accounting and plain English ("basic authentication", "TOKEN=120/450")
are this product's core vocabulary, so a secret-looking name over an ordinary
word or number is prose, not a leak.
"""

import re
from typing import Callable, cast

PLACEHOLDER = "[redacted]"

# The secret-name vocabulary, stated once: the assignment and option patterns
# below are composed from it so the tables cannot silently diverge.
_SECRET_NAMES = "api[_-]?key|secret|token|password|passwd|credential"
_SECRET_NAME = re.compile(rf"(?i)(?:{_SECRET_NAMES})")
_SECRET_OPTION = re.compile(rf"(?i)--?(?:{_SECRET_NAMES})")


def _credential_value(value: str) -> bool:
    """Whether a value is itself secret-shaped.

    Secret-shaped means mixed character classes over a sustained run
    (hunter2correcthorsebattery, JWT segments, base64) or several dense
    chunks joined by separators (command-line-secret) — the fingerprints of
    machine-issued keys. A single-class run is an English word or a count,
    however secret-named its neighbor, and is left alone.
    """
    return len(value) >= 4 and (
        sum(bool(re.search(cls, value)) for cls in (r"[a-z]", r"[A-Z]", r"[0-9]")) >= 2
        or len(re.findall(r"[A-Za-z0-9]{5,}", value)) >= 2
    )


# Shapes, not names: a credential is recognizable by its own text, and a value
# that only a key-looking name identifies must still pass the value-shape test.


def _redact_named(match: re.Match[str]) -> str:
    """Redact only when the captured value is itself secret-shaped."""
    if _credential_value(match.group("v")):
        return f"{match.group('head')}{match.group('sep')}{match.groupdict().get('q') or ''}{PLACEHOLDER}"
    return match.group(0)


_PATTERNS: tuple[tuple[re.Pattern[str], str | Callable[[re.Match[str]], str]], ...] = (
    # Provider-issued keys with a stable prefix.
    (
        re.compile(
            r"\b(?:sk|pk|rk|npm|ghp|gho|ghu|ghs|ghr|xox[abprs])[-_][A-Za-z0-9_-]{16,}"
            + r"|\bAKIA[A-Z0-9]{16}\b|\bAIza[A-Za-z0-9_-]{30,}"
        ),
        PLACEHOLDER,
    ),
    # Bearer/basic scheme values: one dense token. A 16+ char unbroken token
    # never reads as prose ("authentication" is 14), so no value-shape test
    # beyond length is needed here.
    (re.compile(r"(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]{16,}"), PLACEHOLDER),
    # key=value / "key": "value" where the name says secret — and the value
    # must be secret-shaped too, or token accounting gets eaten.
    (
        re.compile(
            rf"(?i)\b(?P<head>[A-Za-z0-9_]*(?:{_SECRET_NAMES})[A-Za-z0-9_]*)(?P<sep>\"?\s*[:=]\s*\"?)"
            + r"(?P<v>[^\s\"',}]+)"
        ),
        _redact_named,
    ),
    # Shell command options using either --token=value or --token value.
    (
        re.compile(
            rf"(?i)(?P<head>--?(?:{_SECRET_NAMES}))(?P<sep>\s+|=\s*)(?P<q>[\"']?)"
            + r"(?P<v>[^\s\"']+)"
        ),
        _redact_named,
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
    Defined through `redact` itself: one table, one value-shape test, so the
    scan gate and the redaction can never disagree about what is a secret.
    """
    return redact(text) != text


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
