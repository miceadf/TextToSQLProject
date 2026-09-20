"""Synthetic tests for the private review-queue snapshot boundary.

Every test uses synthetic run/request IDs and fixed `RepairSummary`
fixtures only; no real questions, SQL, rows, models, DB, network,
Ollama, or private recording bundles. No actual CNU data.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
from pathlib import Path

import pytest

from backend.adaptive.harness import (
    HarnessErrorCode,
    HarnessStage,
    HarnessStatus,
    InMemoryReviewQueue,
    RepairSummary,
    ReviewQueueStatus,
)
from backend.adaptive.harness import review_queue_storage as storage
from backend.adaptive.harness.repair import REPAIR_PROVIDER_ERROR
from backend.adaptive.harness.review_queue import (
    REVIEW_QUEUE_VERSION,
    ReviewQueueEntry,
)
from backend.adaptive.harness.review_queue_storage import (
    REVIEW_QUEUE_SNAPSHOT_VERSION,
    REVIEW_QUEUE_STORAGE_TOOL_VERSION,
    ReviewQueueSnapshot,
    ReviewQueueSnapshotError,
    ReviewQueueSnapshotErrorCode,
    load_review_queue_snapshot,
    preflight_review_queue_snapshot,
    snapshot_payload_sha256,
    write_review_queue_snapshot,
)

RUN_ID = "synth-run-6-05-a"
OTHER_RUN_ID = "synth-run-6-05-b"
SECRET_LIKE_RUN = "AKIAIOSFODNN7_SYNTH_Q7F3A_SECRET_RUN"
SECRET_LIKE_REQUEST = "AKIAIOSFODNN7_SYNTH_Q7F3A_SECRET_REQ"
PATH_LIKE_ID = "..\\synth_q7f3a\\secret\\request-id"


@pytest.fixture
def repository(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    (root / ".gitignore").write_text(".schema-adaptive/\n", encoding="utf-8")
    return root


def snapshot_path(repository: Path, name: str = "review-snapshot.json") -> Path:
    return repository / ".schema-adaptive" / "review-queue" / name


def _failed_summary(code: str, stage: HarnessStage | None) -> RepairSummary:
    return RepairSummary(
        initial_status=HarnessStatus.FAILED,
        initial_stage=HarnessStage.IR_VALIDATED,
        initial_code=HarnessErrorCode.UNKNOWN_FIELD.value,
        repair_attempted=True,
        repair_attempts=1,
        final_status=HarnessStatus.FAILED,
        final_stage=stage,
        final_code=code,
        succeeded=False,
    )


def _success_summary() -> RepairSummary:
    return RepairSummary(
        initial_status=HarnessStatus.SUCCEEDED,
        initial_stage=HarnessStage.RESULT_VALIDATED,
        initial_code=None,
        repair_attempted=False,
        repair_attempts=0,
        final_status=HarnessStatus.SUCCEEDED,
        final_stage=HarnessStage.RESULT_VALIDATED,
        final_code=None,
        succeeded=True,
    )


def _queue_with_failures() -> InMemoryReviewQueue:
    queue = InMemoryReviewQueue()
    queue.enqueue(
        _failed_summary(
            HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED
        ),
        model_run_id=RUN_ID,
        request_id="synth-request-6-05-1",
    )
    queue.enqueue(
        _failed_summary(HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED),
        model_run_id=RUN_ID,
        request_id="synth-request-6-05-2",
    )
    queue.enqueue(
        _failed_summary(
            HarnessErrorCode.EXECUTION_TIMEOUT.value, HarnessStage.EXECUTED
        ),
        model_run_id=OTHER_RUN_ID,
        request_id="synth-request-6-05-3",
    )
    return queue


def test_success_not_registered_failures_round_trip_in_order(
    repository: Path,
) -> None:
    queue = InMemoryReviewQueue()
    success = queue.enqueue(
        _success_summary(), model_run_id=RUN_ID, request_id="synth-request-6-05-ok"
    )
    assert success.status is ReviewQueueStatus.NOT_ELIGIBLE
    queue.enqueue(
        _failed_summary(
            HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED
        ),
        model_run_id=RUN_ID,
        request_id="synth-request-6-05-1",
    )
    queue.enqueue(
        _failed_summary(HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED),
        model_run_id=RUN_ID,
        request_id="synth-request-6-05-2",
    )
    assert len(queue) == 2
    target = snapshot_path(repository)
    written = write_review_queue_snapshot(
        target, queue, repository_root=repository
    )
    assert written.is_file()
    loaded = load_review_queue_snapshot(target, repository_root=repository)
    assert loaded.queue_version == REVIEW_QUEUE_VERSION
    assert loaded.schema_version == REVIEW_QUEUE_SNAPSHOT_VERSION
    assert loaded.entry_count == 2
    assert len(loaded.entries) == 2
    before = queue.list_entries()
    for stored, returned in zip(before, loaded.entries, strict=True):
        assert stored == returned
    assert loaded.entries[0].final_code == HarnessErrorCode.UNKNOWN_FIELD.value
    assert loaded.entries[0].final_stage is HarnessStage.IR_VALIDATED
    assert loaded.entries[1].final_code == HarnessErrorCode.UNSAFE_SQL.value
    assert loaded.entries[1].final_stage is HarnessStage.SQL_VALIDATED
    # Hash matches the canonical payload rule.
    raw = json.loads(target.read_bytes().decode("utf-8"))
    payload = {k: raw[k] for k in raw if k != "payload_sha256"}
    assert raw["payload_sha256"] == snapshot_payload_sha256(payload)
    assert loaded.payload_sha256 == raw["payload_sha256"]
    # Loader mutates no queue.
    assert len(queue) == 2


def test_empty_queue_snapshot_round_trip(repository: Path) -> None:
    queue = InMemoryReviewQueue()
    target = snapshot_path(repository, "empty-snapshot.json")
    write_review_queue_snapshot(target, queue, repository_root=repository)
    loaded = load_review_queue_snapshot(target, repository_root=repository)
    assert loaded.entry_count == 0
    assert loaded.entries == []
    assert len(queue) == 0


def test_same_input_same_bytes_and_hash_order_change_changes_hash(
    repository: Path,
) -> None:
    first = _queue_with_failures()
    second = _queue_with_failures()
    path_a = snapshot_path(repository, "a.json")
    path_b = snapshot_path(repository, "b.json")
    write_review_queue_snapshot(path_a, first, repository_root=repository)
    write_review_queue_snapshot(path_b, second, repository_root=repository)
    assert path_a.read_bytes() == path_b.read_bytes()
    assert json.loads(path_a.read_bytes().decode("utf-8"))[
        "payload_sha256"
    ] == json.loads(path_b.read_bytes().decode("utf-8"))["payload_sha256"]

    reversed_queue = InMemoryReviewQueue()
    reversed_queue.enqueue(
        _failed_summary(
            HarnessErrorCode.EXECUTION_TIMEOUT.value, HarnessStage.EXECUTED
        ),
        model_run_id=OTHER_RUN_ID,
        request_id="synth-request-6-05-3",
    )
    reversed_queue.enqueue(
        _failed_summary(HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED),
        model_run_id=RUN_ID,
        request_id="synth-request-6-05-2",
    )
    reversed_queue.enqueue(
        _failed_summary(
            HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED
        ),
        model_run_id=RUN_ID,
        request_id="synth-request-6-05-1",
    )
    path_c = snapshot_path(repository, "c.json")
    write_review_queue_snapshot(path_c, reversed_queue, repository_root=repository)
    assert json.loads(path_c.read_bytes().decode("utf-8"))[
        "payload_sha256"
    ] != json.loads(path_a.read_bytes().decode("utf-8"))["payload_sha256"]


def test_canonical_rule_is_documented_compact_utf8() -> None:
    payload = {
        "schema_version": REVIEW_QUEUE_SNAPSHOT_VERSION,
        "storage_tool_version": REVIEW_QUEUE_STORAGE_TOOL_VERSION,
        "queue_version": REVIEW_QUEUE_VERSION,
        "entry_count": 0,
        "entries": [],
    }
    expected = hashlib.sha256(
        json.dumps(
            payload,
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
            allow_nan=False,
        ).encode("utf-8")
    ).hexdigest()
    assert snapshot_payload_sha256(payload) == expected


def _mutated_bytes(repository: Path, target: Path, mutate) -> Path:
    raw = json.loads(target.read_bytes().decode("utf-8"))
    mutate(raw)
    other = target.parent / ("mutated-" + target.name)
    other.write_text(
        json.dumps(raw, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return other


@pytest.mark.parametrize("field", ["extra-field", "missing-field"])
def test_extra_or_missing_field_rejected(
    repository: Path, field: str
) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)

    def mutate(raw: dict) -> None:
        if field == "extra-field":
            raw["unexpected"] = "synthetic"
        else:
            del raw["entries"]

    other = _mutated_bytes(repository, target, mutate)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


def test_count_mismatch_rejected(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)

    def mutate(raw: dict) -> None:
        raw["entry_count"] = raw["entry_count"] + 1

    other = _mutated_bytes(repository, target, mutate)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.COUNT_MISMATCH.value


def test_hash_mismatch_rejected(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)

    def mutate(raw: dict) -> None:
        raw["payload_sha256"] = "0" * 64

    other = _mutated_bytes(repository, target, mutate)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.HASH_MISMATCH.value


def test_duplicate_json_key_rejected(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    text = target.read_text(encoding="utf-8")
    other = target.parent / "dup-key-snapshot.json"
    other.write_text(
        text.replace('"entry_count"', '"entry_count"', 1).replace(
            '"entry_count": 3', '"entry_count": 3, "entry_count": 3', 1
        ),
        encoding="utf-8",
    )
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


def test_nonfinite_rejected(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    text = target.read_text(encoding="utf-8")
    other = target.parent / "nonfinite-snapshot.json"
    other.write_text(text.replace("3", "Infinity", 1), encoding="utf-8")
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


def test_duplicate_run_request_and_idempotency_rejected(
    repository: Path,
) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    raw = json.loads(target.read_bytes().decode("utf-8"))

    def write_copy(name: str, document: dict) -> Path:
        other = target.parent / name
        other.write_text(
            json.dumps(document, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return other

    # Duplicate run/request combo: copy first entry hashes into second entry
    # and re-sign the payload hash so the duplicate check (not the hash) fires.
    dup_pair = json.loads(json.dumps(raw))
    dup_pair["entries"][1]["model_run_id_sha256"] = dup_pair["entries"][0][
        "model_run_id_sha256"
    ]
    dup_pair["entries"][1]["request_id_sha256"] = dup_pair["entries"][0][
        "request_id_sha256"
    ]
    payload = {k: dup_pair[k] for k in dup_pair if k != "payload_sha256"}
    dup_pair["payload_sha256"] = snapshot_payload_sha256(payload)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(
            write_copy("dup-pair-snapshot.json", dup_pair),
            repository_root=repository,
        )
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value

    # Duplicate idempotency key across distinct run/request pairs.
    dup_key = json.loads(json.dumps(raw))
    dup_key["entries"][1]["idempotency_key"] = dup_key["entries"][0][
        "idempotency_key"
    ]
    payload = {k: dup_key[k] for k in dup_key if k != "payload_sha256"}
    dup_key["payload_sha256"] = snapshot_payload_sha256(payload)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(
            write_copy("dup-key2-snapshot.json", dup_key),
            repository_root=repository,
        )
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


@pytest.mark.parametrize(
    "field,value",
    [
        ("queue_version", "review-queue-v9"),
        ("schema_version", "review-queue-snapshot-v9"),
        ("storage_tool_version", "review-queue-storage-v9"),
    ],
)
def test_bad_versions_rejected(
    repository: Path, field: str, value: str
) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)

    def mutate(raw: dict) -> None:
        raw[field] = value

    other = _mutated_bytes(repository, target, mutate)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


def test_unknown_code_stage_disposition_rejected(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    raw = json.loads(target.read_bytes().decode("utf-8"))

    def write_resigned(name: str, mutate_entry) -> Path:
        document = json.loads(json.dumps(raw))
        mutate_entry(document["entries"][0])
        payload = {k: document[k] for k in document if k != "payload_sha256"}
        document["payload_sha256"] = snapshot_payload_sha256(payload)
        other = target.parent / name
        other.write_text(
            json.dumps(document, ensure_ascii=False, sort_keys=True),
            encoding="utf-8",
        )
        return other

    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(
            write_resigned(
                "bad-code.json",
                lambda entry: entry.update({"final_code": "SOME_NEW_CODE"}),
            ),
            repository_root=repository,
        )
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value

    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(
            write_resigned(
                "bad-disposition.json",
                lambda entry: entry.update({"disposition": "EXECUTED_UNVERIFIED"}),
            ),
            repository_root=repository,
        )
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value

    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(
            write_resigned(
                "bad-stage.json",
                lambda entry: entry.update({"final_stage": "NO_SUCH_STAGE"}),
            ),
            repository_root=repository,
        )
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


def test_repair_provider_error_code_round_trip(repository: Path) -> None:
    queue = InMemoryReviewQueue()
    queue.enqueue(
        RepairSummary(
            initial_status=HarnessStatus.FAILED,
            initial_stage=HarnessStage.IR_GENERATED,
            initial_code=HarnessErrorCode.MODEL_INVALID_OUTPUT.value,
            repair_attempted=True,
            repair_attempts=1,
            final_status=HarnessStatus.FAILED,
            final_stage=None,
            final_code=REPAIR_PROVIDER_ERROR,
            succeeded=False,
        ),
        model_run_id=RUN_ID,
        request_id="synth-request-6-05-provider",
    )
    target = snapshot_path(repository, "provider-snapshot.json")
    write_review_queue_snapshot(target, queue, repository_root=repository)
    loaded = load_review_queue_snapshot(target, repository_root=repository)
    assert loaded.entries[0].final_code == REPAIR_PROVIDER_ERROR
    assert loaded.entries[0].final_stage is None


def test_outside_root_and_bare_private_root_rejected(
    repository: Path,
) -> None:
    queue = _queue_with_failures()
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        write_review_queue_snapshot(
            repository / "public-snapshot.json", queue, repository_root=repository
        )
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_PATH.value
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        write_review_queue_snapshot(
            repository / ".schema-adaptive", queue, repository_root=repository
        )
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_PATH.value


def test_unignored_private_workspace_rejected(tmp_path: Path) -> None:
    root = tmp_path / "repo"
    root.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(root)],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    queue = _queue_with_failures()
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        write_review_queue_snapshot(
            root / ".schema-adaptive" / "review-queue" / "snapshot.json",
            queue,
            repository_root=root,
        )
    assert (
        caught.value.code
        == ReviewQueueSnapshotErrorCode.PRIVATE_PATH_NOT_IGNORED.value
    )


def test_git_tracked_target_rejected(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository, "tracked-snapshot.json")
    write_review_queue_snapshot(target, queue, repository_root=repository)
    subprocess.run(
        ["git", "-C", str(repository), "add", "-f",
         ".schema-adaptive/review-queue/tracked-snapshot.json"],
        check=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    # The same file is now Git-tracked: preflight and strict load refuse it
    # through the shared bundle validator (no weaker parallel check).
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        preflight_review_queue_snapshot(target, repository_root=repository)
    assert (
        caught.value.code == ReviewQueueSnapshotErrorCode.GIT_TRACKED_TARGET.value
    )
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(target, repository_root=repository)
    assert (
        caught.value.code == ReviewQueueSnapshotErrorCode.GIT_TRACKED_TARGET.value
    )


def test_symlink_redirect_rejected(repository: Path) -> None:
    private = repository / ".schema-adaptive"
    private.mkdir(exist_ok=True)
    redirected = private / "redirected"
    redirected.mkdir(exist_ok=True)
    link = private / "review-link.json"
    try:
        link.symlink_to(redirected / "target.json")
    except (OSError, NotImplementedError):
        pytest.skip("file symlinks unavailable")
    queue = _queue_with_failures()
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        write_review_queue_snapshot(
            link, queue, repository_root=repository
        )
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_PATH.value


def test_no_overwrite_preserves_previous_bytes(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    before = target.read_bytes()
    other = InMemoryReviewQueue()
    other.enqueue(
        _failed_summary(
            HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED
        ),
        model_run_id=OTHER_RUN_ID,
        request_id="synth-request-6-05-other",
    )
    with pytest.raises(FileExistsError):
        write_review_queue_snapshot(target, other, repository_root=repository)
    assert target.read_bytes() == before
    assert not list(target.parent.glob(".snapshot-*.tmp"))


def test_publish_failure_preserves_previous_and_cleans_temp(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    before = target.read_bytes()

    def fail_link(_temporary: str, _target: str) -> None:
        raise OSError("PRIVATE_PATH_OR_PAYLOAD")

    monkeypatch.setattr(os, "link", fail_link)
    other = InMemoryReviewQueue()
    other.enqueue(
        _failed_summary(
            HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED
        ),
        model_run_id=OTHER_RUN_ID,
        request_id="synth-request-6-05-publish",
    )
    fresh = snapshot_path(repository, "fresh-snapshot.json")
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        write_review_queue_snapshot(fresh, other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.STORAGE_ERROR.value
    assert str(caught.value) == "STORAGE_ERROR"
    assert target.read_bytes() == before
    assert not list(target.parent.glob(".snapshot-*.tmp"))


def test_cleanup_failure_does_not_expose_private_path(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)

    def fail_link(_temporary: str, _target: str) -> None:
        raise OSError("PRIVATE_PATH_OR_PAYLOAD")

    original_unlink = Path.unlink

    def fail_temp_unlink(path: Path, *args, **kwargs) -> None:
        if path.name.startswith(".snapshot-"):
            raise OSError("PRIVATE_TEMP_PATH")
        original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "link", fail_link)
    monkeypatch.setattr(Path, "unlink", fail_temp_unlink)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        write_review_queue_snapshot(target, queue, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.STORAGE_ERROR.value
    assert str(caught.value) == "STORAGE_ERROR"


def test_hardlinked_snapshot_rejected_by_loader(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    external = repository / ".schema-adaptive" / "external-hardlink.json"
    external.write_bytes(target.read_bytes())
    target.unlink()
    try:
        os.link(external, target)
    except OSError:
        pytest.skip("hard links unavailable")
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(target, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


def test_raw_ids_and_paths_leave_no_trace(
    repository: Path, capsys: pytest.CaptureFixture, caplog: pytest.LogCaptureFixture
) -> None:
    import io
    from contextlib import redirect_stdout

    queue = InMemoryReviewQueue()
    queue.enqueue(
        _failed_summary(
            HarnessErrorCode.UNKNOWN_FIELD.value, HarnessStage.IR_VALIDATED
        ),
        model_run_id=SECRET_LIKE_RUN,
        request_id=SECRET_LIKE_REQUEST,
    )
    queue.enqueue(
        _failed_summary(HarnessErrorCode.UNSAFE_SQL.value, HarnessStage.SQL_VALIDATED),
        model_run_id=PATH_LIKE_ID,
        request_id="synth-request-6-05-trace",
    )
    target = snapshot_path(repository)
    buffer = io.StringIO()
    with caplog.at_level(logging.DEBUG):
        with redirect_stdout(buffer):
            write_review_queue_snapshot(
                target, queue, repository_root=repository
            )
            loaded = load_review_queue_snapshot(
                target, repository_root=repository
            )
            # Fixed metadata only on stdout: counts and codes, never raw IDs.
            print(loaded.entry_count)
            print(loaded.entries[0].final_code)
    assert loaded.entry_count == 2
    file_text = target.read_bytes().decode("utf-8")
    assert str(target) not in file_text
    captured = (
        buffer.getvalue()
        + file_text
        + str(loaded)
        + caplog.text
    )
    assert SECRET_LIKE_RUN not in captured
    assert SECRET_LIKE_REQUEST not in captured
    assert PATH_LIKE_ID not in captured
    assert "SELECT" not in captured
    # Fixed errors carry no raw content either.
    with pytest.raises(FileExistsError) as exists:
        write_review_queue_snapshot(target, queue, repository_root=repository)
    assert SECRET_LIKE_RUN not in str(exists.value)
    try:
        load_review_queue_snapshot(
            target.parent / "missing-snapshot.json", repository_root=repository
        )
        raise AssertionError("missing snapshot expected")
    except ReviewQueueSnapshotError as exc:
        assert SECRET_LIKE_RUN not in str(exc)
        assert str(repository) not in str(exc)
    capsys.readouterr()


def test_storage_module_makes_no_model_db_or_network_calls() -> None:
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(storage))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                imported.add(alias.name.split(".")[0].lower())
        elif isinstance(node, ast.ImportFrom):
            if node.module:
                imported.add(node.module.split(".")[0].lower())
    for token in ("sqlite3", "socket", "urllib", "requests", "ollama"):
        assert token not in imported


def test_preflight_checks_shared_policy_without_writes(
    repository: Path,
) -> None:
    preflight_review_queue_snapshot(
        snapshot_path(repository), repository_root=repository
    )
    assert not snapshot_path(repository).exists()
    with pytest.raises(ReviewQueueSnapshotError):
        preflight_review_queue_snapshot(
            repository / "public-snapshot.json", repository_root=repository
        )


def _resigned_copy(target: Path, name: str, mutate_entry) -> Path:
    """Mutate one entry, then recompute idempotency key and payload hash.

    Re-signing defeats naive hash/idempotency checks, so only the shared
    critique-table semantic validation can still reject the result.
    """
    document = json.loads(target.read_bytes().decode("utf-8"))
    entry = document["entries"][0]
    mutate_entry(entry)
    stage = entry["final_stage"] if entry["final_stage"] is not None else "NONE"
    entry["idempotency_key"] = (
        f"{REVIEW_QUEUE_VERSION}:{entry['model_run_id_sha256']}"
        f":{entry['request_id_sha256']}:{entry['disposition']}"
        f":{entry['final_code']}:{stage}"
        f":{int(entry['repair_attempted'])}:{int(entry['recovered_after_repair'])}"
    )
    payload = {k: document[k] for k in document if k != "payload_sha256"}
    document["payload_sha256"] = snapshot_payload_sha256(payload)
    other = target.parent / name
    other.write_text(
        json.dumps(document, ensure_ascii=False, sort_keys=True), encoding="utf-8"
    )
    return other


def test_resigned_wrong_but_eligible_disposition_rejected(
    repository: Path,
) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    # UNKNOWN_FIELD/IR_VALIDATED classifies as MODEL_REVIEW, not CONTEXT_REVIEW.
    other = _resigned_copy(
        target,
        "resigned-disposition.json",
        lambda entry: entry.update({"disposition": "CONTEXT_REVIEW"}),
    )
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value
    assert str(caught.value) == "INVALID_CONTRACT"


def test_resigned_invalid_request_as_operational_rejected(
    repository: Path,
) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)

    def mutate(entry: dict) -> None:
        entry.update({
            "disposition": "MODEL_REVIEW",
            "final_code": HarnessErrorCode.INVALID_REQUEST.value,
            "final_stage": HarnessStage.REQUEST_VALIDATED.value,
            "repair_attempted": False,
            "recovered_after_repair": False,
        })

    other = _resigned_copy(target, "resigned-invalid-request.json", mutate)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


def test_resigned_repair_provider_error_with_stage_rejected(
    repository: Path,
) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)

    def mutate(entry: dict) -> None:
        entry.update({
            "disposition": "MODEL_REVIEW",
            "final_code": REPAIR_PROVIDER_ERROR,
            "final_stage": HarnessStage.IR_VALIDATED.value,
        })

    other = _resigned_copy(target, "resigned-provider-stage.json", mutate)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


def test_resigned_recovered_flag_rejected(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    other = _resigned_copy(
        target,
        "resigned-recovered.json",
        lambda entry: entry.update({"recovered_after_repair": True}),
    )
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        load_review_queue_snapshot(other, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value


def test_write_rejects_semantically_forged_entries(
    repository: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    queue = _queue_with_failures()
    good = queue.list_entries()[0]
    forged_dict = good.model_dump(mode="json")
    # Shape-consistent but semantically impossible: UNKNOWN_FIELD with a
    # resigned CONTEXT_REVIEW disposition must fail before any write.
    forged_dict["disposition"] = "CONTEXT_REVIEW"
    forged_dict["idempotency_key"] = (
        f"{REVIEW_QUEUE_VERSION}:{forged_dict['model_run_id_sha256']}"
        f":{forged_dict['request_id_sha256']}:CONTEXT_REVIEW"
        f":{forged_dict['final_code']}:{forged_dict['final_stage']}"
        f":{int(forged_dict['repair_attempted'])}"
        f":{int(forged_dict['recovered_after_repair'])}"
    )
    forged = ReviewQueueEntry.model_validate(forged_dict)
    monkeypatch.setattr(queue, "list_entries", lambda: (forged,))
    target = snapshot_path(repository)
    with pytest.raises(ReviewQueueSnapshotError) as caught:
        write_review_queue_snapshot(target, queue, repository_root=repository)
    assert caught.value.code == ReviewQueueSnapshotErrorCode.INVALID_CONTRACT.value
    assert not target.exists()
    assert not list(target.parent.glob(".snapshot-*.tmp"))


def test_loaded_snapshot_is_defensive_copy(repository: Path) -> None:
    queue = _queue_with_failures()
    target = snapshot_path(repository)
    write_review_queue_snapshot(target, queue, repository_root=repository)
    loaded = load_review_queue_snapshot(target, repository_root=repository)
    assert isinstance(loaded, ReviewQueueSnapshot)
    with pytest.raises(Exception):
        loaded.entry_count = 99  # type: ignore[misc]
    again = load_review_queue_snapshot(target, repository_root=repository)
    assert again == loaded
    assert again is not loaded
