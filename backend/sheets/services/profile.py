"""Describe each column: its type, how full it is, what its values look like.

The profile is what the LLM sees when it proposes a schema (headers and a few
samples, never whole rows), what the Columns step shows, and what
`describe_spreadsheet` hands the agent so it can write correct SQL: the exact
category spellings and the numeric and date ranges.
"""
from __future__ import annotations

import datetime as dt
import re
from collections import Counter

from common.arabic import ar_normalize

TYPES = ("text", "integer", "number", "date", "boolean", "category", "id", "email", "phone")
SAMPLE_COUNT = 8
# At most this many distinct values and the column is listed in full: the
# agent needs exact spellings to filter on them.
CATEGORY_MAX_DISTINCT = 60
TYPE_SHARE = 0.95

TRUE_WORDS = {"yes", "true", "y", "نعم", "صح"}
FALSE_WORDS = {"no", "false", "n", "لا", "خطا", "خطأ"}

_NUMBER = re.compile(r"[+-]?(\d{1,3}(,\d{3})+|\d+)(\.\d+)?")
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}")
_PHONE = re.compile(r"\+?[\d\s\-()]{7,20}")
_ID_HEADER = re.compile(
    r"(^|[\s_\-/(])(id|no|num|number|code|ref|serial|#)($|[\s_\-/).])|رقم|كود|الرمز|هوية",
    re.IGNORECASE,
)
_DATE_FORMATS = (
    "%Y-%m-%d", "%Y-%m-%d %H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y/%m/%d",
    "%d/%m/%Y", "%d-%m-%Y", "%d.%m.%Y", "%m/%d/%Y", "%d/%m/%Y %H:%M", "%d/%m/%Y %H:%M:%S",
)


def _fold_digits(text: str) -> str:
    # ar_normalize maps Arabic-Indic digits to ASCII; the Arabic decimal and
    # thousands separators are folded here.
    return ar_normalize(text).replace("٫", ".").replace("٬", ",").strip()


def parse_number(value) -> int | float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        return value
    if not isinstance(value, str):
        return None
    text = _fold_digits(value)
    if not _NUMBER.fullmatch(text):
        return None
    digits = text.lstrip("+-")
    # "0123" is an identifier or a phone number, not one hundred and twenty-three.
    if len(digits) > 1 and digits.startswith("0") and not digits.startswith("0."):
        return None
    number = float(text.replace(",", ""))
    return int(number) if number.is_integer() and "." not in text else number


def parse_date(value) -> str | None:
    """ISO text (YYYY-MM-DD or YYYY-MM-DD HH:MM:SS), or None."""
    if not isinstance(value, str):
        return None
    text = _fold_digits(value)
    for fmt in _DATE_FORMATS:
        try:
            parsed = dt.datetime.strptime(text, fmt)
        except ValueError:
            continue
        if (parsed.hour, parsed.minute, parsed.second) == (0, 0, 0) and "%H" not in fmt:
            return parsed.date().isoformat()
        return parsed.isoformat(sep=" ")
    return None


def parse_bool(value) -> bool | None:
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        word = ar_normalize(value).strip()
        if word in {ar_normalize(w) for w in TRUE_WORDS}:
            return True
        if word in {ar_normalize(w) for w in FALSE_WORDS}:
            return False
    return None


def display(value) -> str:
    if value is None:
        return ""
    if isinstance(value, bool):
        return "Yes" if value else "No"
    if isinstance(value, float):
        return f"{value:.10g}"
    return str(value)


def typed_value(value, column_type: str):
    """The value to store in SQLite for a column of `column_type`.

    A cell that does not parse keeps its text rather than being dropped:
    losing data silently is worse than a mixed column.
    """
    if value is None or value == "":
        return None
    if column_type == "integer" or column_type == "number":
        number = parse_number(value)
        return number if number is not None else display(value)
    if column_type == "boolean":
        flag = parse_bool(value)
        return int(flag) if flag is not None else display(value)
    if column_type == "date":
        if isinstance(value, str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?", value):
            return value
        return parse_date(value) or display(value)
    return display(value)


def _kind(value) -> str:
    if isinstance(value, bool) or parse_bool(value) is not None:
        return "boolean"
    number = parse_number(value)
    if number is not None:
        return "integer" if isinstance(number, int) else "number"
    if isinstance(value, str) and (
        re.fullmatch(r"\d{4}-\d{2}-\d{2}( \d{2}:\d{2}:\d{2})?", value) or parse_date(value)
    ):
        return "date"
    return "text"


def infer_type(header: str, values: list) -> str:
    """One of TYPES for a column's non-empty values."""
    if not values:
        return "text"
    kinds = Counter(_kind(v) for v in values)
    total = len(values)
    distinct = len({display(v) for v in values})
    unique = distinct == total
    id_header = bool(_ID_HEADER.search(header or ""))

    if kinds["boolean"] / total >= TYPE_SHARE:
        return "boolean"
    if kinds["integer"] / total >= TYPE_SHARE:
        return "id" if id_header and unique else "integer"
    if (kinds["integer"] + kinds["number"]) / total >= TYPE_SHARE:
        return "number"
    if kinds["date"] / total >= TYPE_SHARE:
        return "date"
    texts = [display(v) for v in values]
    if sum(1 for t in texts if _EMAIL.fullmatch(t)) / total >= TYPE_SHARE:
        return "email"
    if sum(1 for t in texts if _PHONE.fullmatch(t) and sum(c.isdigit() for c in t) >= 7) / total >= TYPE_SHARE:
        return "phone"
    if id_header and unique:
        return "id"
    if total >= 5 and distinct <= CATEGORY_MAX_DISTINCT and distinct / total <= 0.5:
        return "category"
    return "text"


def profile_column(header: str, values: list) -> dict:
    """Profile of one column. `values` includes empty cells, in row order."""
    present = [v for v in values if v is not None and v != ""]
    total = len(values)
    column_type = infer_type(header, present)
    counts = Counter(display(v) for v in present)
    samples = list(dict.fromkeys(display(v) for v in present))[:SAMPLE_COUNT]

    profile: dict = {
        "header": header,
        "type": column_type,
        "null_ratio": round(1 - len(present) / total, 3) if total else 0.0,
        "distinct": len(counts),
        "samples": samples,
    }
    if len(counts) <= CATEGORY_MAX_DISTINCT and column_type in ("category", "boolean", "text"):
        profile["options"] = [value for value, _ in counts.most_common()]
    if column_type in ("integer", "number"):
        numbers = [n for n in (parse_number(v) for v in present) if n is not None]
        if numbers:
            profile["min"] = min(numbers)
            profile["max"] = max(numbers)
            profile["mean"] = round(sum(numbers) / len(numbers), 4)
    if column_type == "date":
        dates = sorted(d for d in (typed_value(v, "date") for v in present) if isinstance(d, str))
        if dates:
            profile["min"] = dates[0]
            profile["max"] = dates[-1]
    profile["fill_down_suggested"] = _fill_down_suggested(values, column_type, len(counts))
    return profile


def _fill_down_suggested(values: list, column_type: str, distinct: int) -> bool:
    """Blanks under a value, in a grouping column: "10-B" written once over its students."""
    if not values or values[0] in (None, "") or column_type not in ("category", "text"):
        return False
    blanks = sum(1 for v in values if v in (None, ""))
    return blanks / len(values) >= 0.3 and distinct <= CATEGORY_MAX_DISTINCT


def profile_sheet(headers: list[str], rows: list) -> list[dict]:
    """`rows` is [(row_number, values), ...] or the stored [[n, values], ...]."""
    columns = list(zip(*[values for _, values in rows])) if rows else [() for _ in headers]
    return [profile_column(header, list(column)) for header, column in zip(headers, columns)]
