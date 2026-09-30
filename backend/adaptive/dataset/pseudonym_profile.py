"""Reviewed semantic-field policy and local secret-key loading."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import yaml

from backend.adaptive.ontology.contracts import DataType, DomainOntology

from .pseudonym_contracts import PseudonymPolicy, PseudonymizationProfile


def load_pseudonymization_profile(
    path: str | Path,
    ontology: DomainOntology,
) -> PseudonymizationProfile:
    """Load a policy that explicitly covers only known ontology fields."""

    try:
        with Path(path).open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
    except (OSError, UnicodeError, yaml.YAMLError) as exc:
        raise ValueError("pseudonymization profile cannot be loaded") from None
    if not isinstance(raw, dict):
        raise ValueError("pseudonymization profile must be a YAML object")
    try:
        profile = PseudonymizationProfile.model_validate(raw)
    except Exception:
        raise ValueError("invalid pseudonymization profile") from None
    if not ontology.accepts_version(profile.ontology_version):
        raise ValueError("pseudonymization profile ontology version mismatch")

    known_fields = ontology.fields
    for item in profile.field_policies:
        field = known_fields.get(item.field)
        if field is None:
            raise ValueError("pseudonymization profile contains an unknown field")
        if item.policy is PseudonymPolicy.PSEUDONYMIZE and field.data_type is not DataType.STRING:
            raise ValueError("pseudonymization requires a string ontology field")
    return profile


def read_secret_key(path: str | Path, repository_root: str | Path) -> bytes:
    """Read at least 32 key bytes, rejecting Git-tracked repository keys.

    The key and its path are never inserted into error messages.
    """

    try:
        supplied_path = Path(path).absolute()
        key_path = supplied_path.resolve(strict=True)
        root = Path(repository_root).resolve(strict=True)
    except OSError:
        raise ValueError("secret key file cannot be resolved") from None
    if not key_path.is_file():
        raise ValueError("secret key path is not a regular file")

    # Check both the supplied path (possibly a tracked symlink) and its target.
    # Otherwise a tracked symlink pointing outside the worktree bypasses policy.
    for candidate in {supplied_path, key_path}:
        try:
            relative = candidate.relative_to(root)
        except ValueError:
            continue
        if ".git" in relative.parts:
            raise ValueError("secret key cannot be stored in Git metadata")
        try:
            listed = subprocess.run(
                ["git", "ls-files", "--cached", "--error-unmatch", "--", relative.as_posix()],
                cwd=root,
                capture_output=True,
                check=False,
            )
        except OSError:
            raise ValueError("cannot verify whether secret key is Git-tracked") from None
        if listed.returncode == 0:
            raise ValueError("secret key file must not be Git-tracked")
        if listed.returncode != 1:
            raise ValueError("cannot verify whether secret key is Git-tracked")
    # A local untracked hardlink can alias a tracked key without sharing its path.
    try:
        tracked = subprocess.run(
            ["git", "ls-files", "-z"], cwd=root, capture_output=True, check=False
        )
    except OSError:
        raise ValueError("cannot verify whether secret key is Git-tracked") from None
    if tracked.returncode != 0:
        raise ValueError("cannot verify whether secret key is Git-tracked")
    for relative_bytes in tracked.stdout.split(b"\0"):
        if not relative_bytes:
            continue
        tracked_path = root / os.fsdecode(relative_bytes)
        try:
            if tracked_path.is_file() and os.path.samefile(tracked_path, key_path):
                raise ValueError("secret key file must not be Git-tracked")
        except OSError:
            continue
    try:
        key = key_path.read_bytes()
    except OSError:
        raise ValueError("secret key file cannot be read") from None
    if len(key) < 32:
        raise ValueError("secret key must contain at least 32 bytes")
    return key
