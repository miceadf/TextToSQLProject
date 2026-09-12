"""Deterministic, domain-separated HMAC tokens for sensitive IR values."""

from __future__ import annotations

import hashlib
import hmac
import json
import re
import unicodedata

from .pseudonym_contracts import PSEUDONYM_ALGORITHM_VERSION, PseudonymizationProfile


_NAMESPACE_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_DOMAIN_RE = re.compile(r"^[a-z][a-z0-9]*(?:-[a-z0-9]+)*$")
_MIN_TOKEN_HEX_LENGTH = 12
_HEX_EXTENSION_STEP = 2


def _digest(
    domain: str,
    namespace: str,
    value: str,
    key: bytes,
    profile: PseudonymizationProfile,
) -> str:
    if len(key) < 32:
        raise ValueError("secret key must contain at least 32 bytes")
    if not _DOMAIN_RE.fullmatch(domain) or not _NAMESPACE_RE.fullmatch(namespace):
        raise ValueError("invalid pseudonym token domain or namespace")
    payload = json.dumps(
        [
            PSEUDONYM_ALGORITHM_VERSION,
            profile.profile_id,
            profile.version,
            domain,
            namespace,
            unicodedata.normalize("NFC", value),
        ],
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")
    return hmac.new(key, payload, hashlib.sha256).hexdigest().upper()


def generate_token_map(
    pairs: set[tuple[str, str]],
    key: bytes,
    profile: PseudonymizationProfile,
) -> dict[tuple[str, str], str]:
    """Generate `<NAMESPACE_12HEX>` tokens; extend *all* collision members.

    Input iteration order cannot affect token assignment. NFC-equivalent input
    values intentionally receive the same token; lexicon collision detection is
    a separate semantic safety check performed by the transformer.
    """

    entries: dict[tuple[str, str], str] = {}
    for namespace, value in pairs:
        if not isinstance(value, str):
            raise ValueError("pseudonym value must be a string")
        normalized = unicodedata.normalize("NFC", value)
        entries[(namespace, normalized)] = _digest(
            "sensitive-value", namespace, normalized, key, profile
        )

    lengths = {pair: _MIN_TOKEN_HEX_LENGTH for pair in entries}
    while True:
        by_token: dict[str, list[tuple[str, str]]] = {}
        for pair, digest in entries.items():
            namespace = pair[0]
            prefix = namespace.upper().replace("-", "_")
            token = f"<{prefix}_{digest[:lengths[pair]]}>"
            by_token.setdefault(token, []).append(pair)
        collisions = [group for group in by_token.values() if len(group) > 1]
        if not collisions:
            break
        for group in collisions:
            for pair in group:
                if lengths[pair] == 64:
                    raise ValueError("full pseudonym digest collision")
                lengths[pair] = min(64, lengths[pair] + _HEX_EXTENSION_STEP)

    return {
        pair: f"<{pair[0].upper().replace('-', '_')}_{entries[pair][:lengths[pair]]}>"
        for pair in sorted(entries)
    }


def lineage_token(
    domain: str,
    value: str,
    key: bytes,
    profile: PseudonymizationProfile,
) -> str:
    """Make a non-reversible lineage handle in a separate HMAC domain."""

    if not isinstance(value, str):
        raise ValueError("lineage value must be a string")
    return "LIN_" + _digest("lineage", domain, value, key, profile)
