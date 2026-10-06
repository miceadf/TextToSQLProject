"""Chat time expressions: closed Korean rules, the model's hint and a repair (Phase 8-25).

The chat model sometimes drops "오전", reads "3시 이후에 시작하는" as an end
time, or turns "이후" into a strict ``>``. Time words are language, not a
school's data, so a small closed rule set reads them from the question:

- the conditions go into the one model call next to the value hints;
- a closed repair aligns the model's start/end time conditions with them, and
  adds them when the model returned no time condition at all (the one case a
  chat repair adds a condition: the question states it, the rules alone fix
  it, and the "understood" card shows it);
- a condition on a time field the database lacks is answered as unsupported.

Anything the rules cannot read with certainty gives no condition and is left
to the model. The repaired output still passes the unchanged QueryIR
validation, compiler, policy and executor.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

START_TIME = "Schedule.start_time"
END_TIME = "Schedule.end_time"
TIME_FIELDS = frozenset({START_TIME, END_TIME})
MAX_CONDITIONS = 2  # more time expressions than this are left to the model

__all__ = [
    "END_TIME",
    "START_TIME",
    "TIME_FIELDS",
    "TimeCondition",
    "apply_time_conditions",
    "find_time_conditions",
]


@dataclass(frozen=True)
class TimeCondition:
    text: str
    field: str
    operator: str
    value: str  # "HH:MM"

    def payload(self) -> dict[str, str]:
        return {"text": self.text, "field": self.field, "operator": self.operator, "value": self.value}

    def filter(self) -> dict[str, str]:
        return {"field": self.field, "operator": self.operator, "value": self.value}


_FIELD_WORDS = "시작|끝|종료|마치|마감"
_NOUNS = "수업|강의|과목|강좌"
# A clock time: "3시", "오후 3시 반", "9시 30분", "15:00". After "시" only a
# direction, a particle, a field word or a noun may follow, so "3시간" or
# "2시험" are not times.
_CLOCK = re.compile(
    r"(?<![\d:])"
    r"(?:(?P<part>오전|오후|아침|저녁|밤)\s*)?"
    r"(?:(?P<hh>\d{1,2}):(?P<mm>\d{2})(?!\d)"
    r"|(?P<h>\d{1,2})\s*시(?:\s*(?P<m>\d{1,2})\s*분|\s*(?P<half>반))?"
    r"(?=\s|$|[?.!,]|이후|이전|부터|까지|전|후|뒤|에|" + _FIELD_WORDS + "|" + _NOUNS + "))"
)
_HALF_DAY = re.compile(r"(?P<part>오전|오후)(?!\s*\d)")
_DIRECTION = re.compile(r"\s*(?P<word>이후|이전|부터|까지|전(?!공)|후|뒤)")
_PLAIN_END = re.compile(r"\s*(?:에\s*)?(?:" + _FIELD_WORDS + "|" + _NOUNS + r")|\s*에?\s*(?:$|[?.!,])")
_FIELD_AFTER = re.compile(r"\s*(?:에|에서|에는)?\s*(?P<word>" + _FIELD_WORDS + ")")
_FIELD_BEFORE = re.compile(
    r"(?P<word>" + _FIELD_WORDS + r")[가-힣]{0,2}\s*(?:시간|시각)?\s*(?:이|은|는|가)?\s*$"
)
_RANGE_MARK = re.compile(r"[~\-–]")
# "오전 수업 말고 오후 수업": how the mentions relate is beyond the rules.
_NEGATION = re.compile(r"말고|빼고|제외|아닌|않는|아니")

_OPERATORS = {
    "이후": "greater_than_or_equal",
    "부터": "greater_than_or_equal",
    "후": "greater_than_or_equal",
    "뒤": "greater_than_or_equal",
    "이전": "less_than",
    "전": "less_than",
    "까지": "less_than_or_equal",
}


def _hour(part: str | None, hour: int, padded: bool) -> int | None:
    if part in ("오후", "저녁", "밤"):
        if part == "오후" and hour == 12:
            return 12
        return hour + 12 if 1 <= hour <= 11 else None  # "밤 12시" (midnight) is not read
    if part in ("오전", "아침"):
        return hour if 1 <= hour <= 11 else None
    if 13 <= hour <= 23 or padded:
        return hour if hour <= 23 else None
    if 1 <= hour <= 7:
        return hour + 12  # class hours: a bare "3시" is the afternoon
    return hour if 8 <= hour <= 12 else None


def _clock_value(match: re.Match[str]) -> str | None:
    part = match.group("part")
    if match.group("hh") is not None:
        hour = _hour(part, int(match.group("hh")), match.group("hh").startswith("0"))
        minute = int(match.group("mm"))
    else:
        hour = _hour(part, int(match.group("h")), False)
        minute = 30 if match.group("half") else int(match.group("m") or 0)
    if hour is None or not 0 <= minute <= 59:
        return None
    return f"{hour:02d}:{minute:02d}"


def _field_word(question: str, start: int, end: int) -> str | None:
    """The field a nearby word names: right after the direction, else right before."""
    after = _FIELD_AFTER.match(question, end)
    word = after.group("word") if after else None
    if word is None:
        before = _FIELD_BEFORE.search(question[max(0, start - 12) : start])
        word = before.group("word") if before else None
    if word is None:
        return None
    return START_TIME if word == "시작" else END_TIME


def _overlaps(span: tuple[int, int], taken: list[tuple[int, int]]) -> bool:
    return any(span[0] < other[1] and other[0] < span[1] for other in taken)


def find_time_conditions(question: str) -> list[TimeCondition]:
    """Time conditions the question states, in question order; [] when unsure."""
    if not isinstance(question, str):
        return []
    found: list[tuple[int, TimeCondition | None]] = []
    taken: list[tuple[int, int]] = []
    for match in _CLOCK.finditer(question):
        taken.append(match.span())
        before = question[max(0, match.start() - 2) : match.start()]
        after = question[match.end() : match.end() + 2]
        if _RANGE_MARK.search(before) or _RANGE_MARK.search(after):
            return []  # "3시~5시": a range the rules do not read
        value = _clock_value(match)
        direction = _DIRECTION.match(question, match.end())
        if direction is not None:
            operator: str | None = _OPERATORS[direction.group("word")]
            end = direction.end()
        elif _PLAIN_END.match(question, match.end()):
            operator, end = "equals", match.end()
        else:
            operator, end = None, match.end()  # "3시 넘어서": unknown direction
        field = _field_word(question, match.start(), end)
        if field is None and operator is not None:
            if operator == "less_than":
                operator = None  # "3시 전 수업": start or end, left to the model
            else:
                field = END_TIME if operator == "less_than_or_equal" else START_TIME
        condition = None
        if value is not None and operator is not None and field is not None:
            condition = TimeCondition(question[match.start() : end].strip(), field, operator, value)
        found.append((match.start(), condition))
    for match in _HALF_DAY.finditer(question):
        if _overlaps(match.span(), taken):
            continue
        field = _field_word(question, match.start(), match.end()) or START_TIME
        if match.group("part") == "오전":
            condition = TimeCondition("오전", field, "less_than", "12:00")
        else:
            condition = TimeCondition("오후", field, "greater_than_or_equal", "12:00")
        found.append((match.start(), condition))
    if found and _NEGATION.search(question):
        return []
    conditions: list[TimeCondition] = []
    for _position, condition in sorted(found, key=lambda item: item[0]):
        if condition is None:
            return []  # one time word the rules cannot read: all left to the model
        if condition not in conditions:
            conditions.append(condition)
    if len(conditions) > MAX_CONDITIONS or not _satisfiable(conditions):
        return []
    return conditions


def _satisfiable(conditions: list[TimeCondition]) -> bool:
    """False when the conditions on one field leave no time ("오전" and "오후")."""
    for field in TIME_FIELDS:
        low, low_strict, high, high_strict = "", False, "99:99", False
        for item in (condition for condition in conditions if condition.field == field):
            if item.operator in ("equals", "greater_than_or_equal") and item.value > low:
                low, low_strict = item.value, False
            if item.operator in ("equals", "less_than_or_equal") and item.value < high:
                high, high_strict = item.value, False
            if item.operator == "less_than" and item.value <= high:
                high, high_strict = item.value, True
        if low > high or (low == high and high_strict):
            return False
    return True


_MODEL_TIME = re.compile(r"\s*(\d{1,2}):?(\d{2})(?::00)?\s*")


def _model_value(value: Any) -> str | None:
    match = _MODEL_TIME.fullmatch(value) if isinstance(value, str) else None
    return f"{int(match.group(1)):02d}:{match.group(2)}" if match else None


def apply_time_conditions(output: Any, conditions: list[TimeCondition]) -> Any:
    """Align the model's time conditions with the question's; anything unexpected stays."""
    if not conditions or not isinstance(output, dict):
        return output
    filters = output.get("filters", [])
    if not isinstance(filters, list):
        return output
    timed = [
        position
        for position, item in enumerate(filters)
        if isinstance(item, dict) and item.get("field") in TIME_FIELDS
    ]
    repaired = list(filters)
    if not timed:
        repaired += [condition.filter() for condition in conditions]
    elif len(timed) == len(conditions):
        left = list(conditions)
        pairs: dict[int, TimeCondition] = {}
        for position in timed:  # the same time first
            value = _model_value(filters[position].get("value"))
            same = next((condition for condition in left if condition.value == value), None)
            if same is not None:
                pairs[position] = same
                left.remove(same)
        for position in timed:  # then question order
            if position not in pairs:
                pairs[position] = left.pop(0)
        for position, condition in pairs.items():
            repaired[position] = {**filters[position], **condition.filter()}
    else:
        return output
    return {**output, "filters": repaired}
