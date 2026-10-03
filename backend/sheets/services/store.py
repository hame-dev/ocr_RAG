"""The spreadsheet as real tables, for counts, sums, averages and filters.

Each spreadsheet document gets its own SQLite file next to its upload, with a
table per sheet. The agent queries it through `run_select`, which is the only
reader and is locked down in layers:

  - the file is opened read-only (`mode=ro`) with `query_only` on;
  - an authorizer allows SELECT, column reads and functions, nothing else:
    no ATTACH (so no other file), no PRAGMA, no writes of any kind;
  - a progress handler aborts a query that runs past its deadline;
  - result rows and cell text are capped.

One file per document means even a creative query can only see the sheet the
ownership check already allowed. No code is executed: Documents mode stays a
mode that cannot run code.
"""
from __future__ import annotations

import os
import sqlite3
import time

from django.conf import settings

from sheets.services.cards import iter_records

SQL_TYPES = {"integer": "INTEGER", "number": "REAL", "boolean": "INTEGER"}
CELL_CHARS = 300
FILE_NAME = "sheets.sqlite"
_ALLOWED_ACTIONS = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
# Functions that touch the outside world or the connection, refused even though
# load_extension is disabled on this connection anyway.
_DENIED_FUNCTIONS = {"load_extension", "readfile", "writefile", "edit", "fts3_tokenizer"}


class QueryError(ValueError):
    """A query that could not run; the message is shown to the model."""


def sqlite_path(document) -> str:
    from documents.services.preprocess import doc_dir

    return os.path.join(doc_dir(document), FILE_NAME)


def _quote(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def build_sqlite(document, sheets) -> str:
    """Write every confirmed sheet to a fresh file, swapped in atomically."""
    path = sqlite_path(document)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.tmp"
    if os.path.exists(tmp):
        os.remove(tmp)
    conn = sqlite3.connect(tmp)
    try:
        for sheet in sheets:
            schema = sheet.schema
            columns = [(c["key"], SQL_TYPES.get(c["type"], "TEXT")) for c in schema["columns"]]
            columns += [(c["key"], "TEXT") for c in schema.get("combine") or []]
            table = _quote(sheet.table_name)
            definition = ", ".join(["_row INTEGER PRIMARY KEY"] + [f"{_quote(k)} {t}" for k, t in columns])
            conn.execute(f"CREATE TABLE {table} ({definition})")
            keys = [k for k, _ in columns]
            placeholders = ", ".join("?" for _ in range(len(keys) + 1))
            conn.executemany(
                f"INSERT INTO {table} VALUES ({placeholders})",
                ([row_number, *[typed.get(k) for k in keys]] for row_number, _shown, typed in iter_records(sheet.rows, schema)),
            )
            indexed = [c["key"] for c in schema["columns"] if c["role"] in ("identifier", "title")]
            indexed += [c["key"] for c in schema.get("combine") or []]
            for key in indexed:
                conn.execute(f"CREATE INDEX {_quote(f'ix_{sheet.table_name}_{key}')} ON {table} ({_quote(key)})")
        conn.commit()
    finally:
        conn.close()
    os.replace(tmp, path)
    return path


def _authorizer(action, arg1, arg2, db_name, trigger):
    if action not in _ALLOWED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == sqlite3.SQLITE_FUNCTION and (arg2 or "").lower() in _DENIED_FUNCTIONS:
        return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def run_select(path: str, sql: str, *, timeout_s: float | None = None, max_rows: int | None = None) -> dict:
    """Run one read-only statement. Raises QueryError with a readable reason."""
    timeout_s = timeout_s or settings.SHEET_QUERY_TIMEOUT_S
    max_rows = max_rows or settings.SHEET_QUERY_MAX_ROWS
    sql = (sql or "").strip().rstrip(";").strip()
    if not sql:
        raise QueryError("the query is empty")
    if not os.path.exists(path):
        raise QueryError("this spreadsheet has no query tables yet; confirm its columns first")

    started = time.monotonic()
    conn = sqlite3.connect(f"file:{path}?mode=ro", uri=True, timeout=1, check_same_thread=False)
    try:
        conn.execute("PRAGMA query_only = ON")
        conn.setlimit(sqlite3.SQLITE_LIMIT_LENGTH, 10_000_000)
        conn.setlimit(sqlite3.SQLITE_LIMIT_ATTACHED, 0)
        conn.set_authorizer(_authorizer)
        deadline = started + timeout_s
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 10_000)
        try:
            cursor = conn.execute(sql)
            fetched = cursor.fetchmany(max_rows + 1)
        except sqlite3.Warning as exc:  # "You can only execute one statement at a time."
            raise QueryError(str(exc)) from exc
        except sqlite3.DatabaseError as exc:
            message = str(exc)
            if "interrupted" in message:
                message = f"the query took longer than {timeout_s:g} s and was stopped; simplify it"
            elif "not authorized" in message:
                message = "only read-only SELECT queries on this spreadsheet's tables are allowed"
            raise QueryError(message) from exc
        if cursor.description is None:
            raise QueryError("only SELECT queries are allowed")
        columns = [d[0] for d in cursor.description]
    finally:
        conn.close()

    truncated = len(fetched) > max_rows
    rows = [[_cell(v) for v in row] for row in fetched[:max_rows]]
    return {
        "columns": columns,
        "rows": rows,
        "row_count": len(rows),
        "truncated": truncated,
        "duration_ms": int((time.monotonic() - started) * 1000),
    }


def _cell(value):
    if isinstance(value, bytes):
        return f"<{len(value)} bytes>"
    if isinstance(value, str) and len(value) > CELL_CHARS:
        return value[:CELL_CHARS] + "…"
    if isinstance(value, float):
        return round(value, 6)
    return value
