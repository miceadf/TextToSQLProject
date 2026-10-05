"""Phase 9 entry: the L1 review table is prepared, never decided, by code."""

from __future__ import annotations

import csv
import io
import json
from pathlib import Path
from typing import Any

import pytest

from backend.adaptive.evaluation import l1_review
from tests.adaptive.test_blind_eval import SECRET_QUESTION, write_questions

FINGERPRINT = l1_review.sets_fingerprint([path for _name, path in l1_review.SETS])


def built_rows() -> list[dict[str, str]]:
    return l1_review.build_rows(l1_review._load_sets(), FINGERPRINT)


def test_every_gold_case_gets_one_row_with_blank_decisions() -> None:
    rows = built_rows()
    assert len(rows) == 70 and len({row["사례 ID"] for row in rows}) == 70
    assert all(row["판정(승인/수정/보류)"] == "" and row["검토자"] == "" for row in rows)
    assert all(row["gold 지문"] == FINGERPRINT for row in rows)
    first = rows[0]
    assert first["보여 줄 항목"] == "과목명, 분반" and first["정답 행 수"] == "6"


def test_flags_point_at_what_needs_a_look() -> None:
    by_id = {row["사례 ID"]: row["자동 확인 메모"] for row in built_rows()}
    assert "빈 결과" in by_id["P2-028"]
    assert "경계값" in by_id["P2-018"]
    assert "질문 표현과 조건 값이 다름" in by_id["P2-015"]  # 수요일 vs WED
    assert by_id["P2-001"] == ""


def build(tmp_path: Path) -> Path:
    assert l1_review.main(["build", "--out", "out", "--root", str(tmp_path)]) == 0
    return tmp_path / "out" / "fixture-v1-review.csv"


def read(path: Path) -> list[dict[str, str]]:
    return list(csv.DictReader(io.StringIO(path.read_text(encoding="utf-8-sig"))))


def write(path: Path, rows: list[dict[str, str]], columns: list[str] | None = None) -> None:
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=columns or list(rows[0]), lineterminator="\n", extrasaction="ignore")
    writer.writeheader()
    writer.writerows(rows)
    path.write_text("﻿" + stream.getvalue(), encoding="utf-8")


def decide(row: dict[str, str], decision: str = "승인", note: str = "") -> None:
    row.update({"판정(승인/수정/보류)": decision, "의견(수정·보류 사유)": note, "검토자": "user", "검토일": "2026-10-07"})


def check(path: Path) -> dict[str, Any]:
    return l1_review.check_filled(path, built_rows(), l1_review.COLUMNS)


def test_outputs_carry_the_gold_fingerprint_and_open_in_excel(tmp_path: Path) -> None:
    path = build(tmp_path)
    markdown = (tmp_path / "out" / "fixture-v1-review.md").read_text(encoding="utf-8")
    assert FINGERPRINT in markdown and "프로그래밍기초" in markdown
    assert path.read_bytes().startswith("﻿".encode("utf-8"))


def test_recording_never_changes_the_gold(tmp_path: Path) -> None:
    path = build(tmp_path)
    rows = read(path)
    for row in rows:
        decide(row)
    write(path, rows)
    assert l1_review.main(["record", str(path)]) == 0
    assert l1_review.sets_fingerprint([p for _name, p in l1_review.SETS]) == FINGERPRINT
    record = json.loads(path.with_suffix(".record.json").read_text(encoding="utf-8"))
    assert record["counts"] == {"승인": 70} and record["gold_fingerprint"] == FINGERPRINT


def test_a_partly_filled_sheet_counts_the_rest_as_unreviewed(tmp_path: Path) -> None:
    path = build(tmp_path)
    rows = read(path)
    decide(rows[0], "수정", "분반 순서")
    decide(rows[1], "보류", "요일 표기")
    write(path, rows)
    assert check(path)["counts"] == {"수정": 1, "보류": 1, "미검토": 68}


@pytest.mark.parametrize(
    ("change", "problem"),
    [
        ({"판정(승인/수정/보류)": "OK"}, "판정"),
        ({"검토자": ""}, "검토자"),
        ({"판정(승인/수정/보류)": "수정"}, "의견"),
        ({"검토일": "10/7"}, "검토일"),
        ({"검토일": ""}, "검토일"),  # Luna 9-01 review: a decision needs its date
        ({"사례 ID": "P2-999"}, "사례 ID"),
    ],
)
def test_malformed_decisions_are_refused(tmp_path: Path, change: dict[str, str], problem: str) -> None:
    path = build(tmp_path)
    rows = read(path)
    decide(rows[0])
    rows[0].update(change)
    write(path, rows)
    with pytest.raises(ValueError) as caught:
        check(path)
    assert problem in str(caught.value)


def test_a_sheet_for_other_gold_is_refused(tmp_path: Path) -> None:
    """Luna 9-01 review: an old sheet must not be recorded against changed gold."""
    path = build(tmp_path)
    rows = read(path)
    for row in rows:
        decide(row)
    rows[3]["질문"] = rows[3]["질문"] + " (예전 문구)"
    write(path, rows)
    with pytest.raises(ValueError) as caught:
        check(path)
    assert "현재 gold와 다름" in str(caught.value)
    rows = read(build(tmp_path / "again"))
    for row in rows:
        decide(row)
        row["gold 지문"] = "0" * 64
    write(path, rows)
    with pytest.raises(ValueError):
        check(path)


def test_a_sheet_without_the_material_is_refused(tmp_path: Path) -> None:
    """Luna 9-01 review: approvals without the questions and gold are not a review."""
    path = build(tmp_path)
    rows = read(path)
    for row in rows:
        decide(row)
    write(path, rows, ["사례 ID", "판정(승인/수정/보류)", "의견(수정·보류 사유)", "검토자", "검토일"])
    with pytest.raises(ValueError) as caught:
        check(path)
    assert "열" in str(caught.value)


def test_a_teammate_sheet_stays_private_and_unprinted(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    write_questions(tmp_path)
    assert l1_review.main(["build", "--blind", "school-a", "--root", str(tmp_path)]) == 0
    out = capsys.readouterr().out
    assert out.strip() == "3 rows" and SECRET_QUESTION not in out
    sheet = tmp_path / ".schema-adaptive" / "blind-evals" / "school-a" / "l1-review.csv"
    assert SECRET_QUESTION in sheet.read_text(encoding="utf-8-sig")
    # a second build never overwrites decisions already written
    assert l1_review.main(["build", "--blind", "school-a", "--root", str(tmp_path)]) == 2
    rows = read(sheet)
    for row in rows:
        decide(row)
    write(sheet, rows)
    assert l1_review.main(["record", str(sheet), "--blind", "school-a", "--root", str(tmp_path)]) == 0
    assert SECRET_QUESTION not in capsys.readouterr().out


def test_formula_like_cells_are_inert_in_excel(tmp_path: Path) -> None:
    """Luna 9-01 review: a question starting with '=' must not run as a formula."""
    path = write_questions(tmp_path)
    data = json.loads(path.read_text(encoding="utf-8"))
    data["questions"][0]["text"] = "=HYPERLINK(1)"
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    assert l1_review.main(["build", "--blind", "school-a", "--root", str(tmp_path)]) == 0
    sheet = tmp_path / ".schema-adaptive" / "blind-evals" / "school-a" / "l1-review.csv"
    assert read(sheet)[0]["질문"] == "'=HYPERLINK(1)"


def test_a_link_in_place_of_the_sheet_is_not_written_through(tmp_path: Path) -> None:
    """Luna 9-01 review: exclusive create refuses an existing (even dangling) link."""
    write_questions(tmp_path)
    sheet = tmp_path / ".schema-adaptive" / "blind-evals" / "school-a" / "l1-review.csv"
    try:
        sheet.symlink_to(tmp_path / "outside.csv")
    except OSError:
        pytest.skip("symbolic links are not available here")
    assert l1_review.main(["build", "--blind", "school-a", "--root", str(tmp_path)]) == 2
    assert not (tmp_path / "outside.csv").exists()
