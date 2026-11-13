from __future__ import annotations

import json
import sqlite3
from typing import Any


def insert_batch(
    conn: sqlite3.Connection,
    *,
    dataset_id: int,
    status: str,
    scanned: int,
    passed: int,
    failed: int,
    quarantined: int,
    released: int,
    report: dict[str, Any],
    created_at: str,
    blocked: bool = False,
    block_summary: str = "",
) -> int:
    cur = conn.execute(
        """
        INSERT INTO inspect_batches(
          dataset_id, status, scanned_count, passed_count, failed_count,
          quarantined_count, released_count, blocked, block_summary, report_json, created_at
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            dataset_id,
            status,
            scanned,
            passed,
            failed,
            quarantined,
            released,
            1 if blocked else 0,
            block_summary,
            json.dumps(report, ensure_ascii=False),
            created_at,
        ),
    )
    return int(cur.lastrowid)


def finalize_batch(
    conn: sqlite3.Connection,
    batch_id: int,
    *,
    status: str,
    released: int,
    blocked: bool,
    block_summary: str,
    report: dict[str, Any],
) -> None:
    conn.execute(
        """
        UPDATE inspect_batches
        SET status=?, released_count=?, blocked=?, block_summary=?, report_json=?
        WHERE id=?
        """,
        (status, released, 1 if blocked else 0, block_summary, json.dumps(report, ensure_ascii=False), batch_id),
    )


def insert_row(
    conn: sqlite3.Connection,
    *,
    batch_id: int,
    row_no: int,
    entity_key: str | None,
    payload: dict[str, Any],
    disposition: str,
    rule_hits: list[dict[str, Any]],
    release_state: str = "released",
) -> None:
    conn.execute(
        """
        INSERT INTO inspect_rows(
          batch_id, row_no, entity_key, payload_json, disposition, release_state, rule_hits_json
        ) VALUES (?,?,?,?,?,?,?)
        """,
        (
            batch_id,
            row_no,
            entity_key,
            json.dumps(payload, ensure_ascii=False),
            disposition,
            release_state,
            json.dumps(rule_hits, ensure_ascii=False),
        ),
    )


def list_batches(conn: sqlite3.Connection, limit: int = 50) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, dataset_id, status, scanned_count, passed_count, failed_count,
               quarantined_count, released_count, blocked, block_summary, report_json, created_at
        FROM inspect_batches ORDER BY id DESC LIMIT ?
        """,
        (limit,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["report"] = json.loads(d.pop("report_json"))
        out.append(d)
    return out


def get_batch(conn: sqlite3.Connection, batch_id: int) -> dict[str, Any] | None:
    row = conn.execute(
        """
        SELECT id, dataset_id, status, scanned_count, passed_count, failed_count,
               quarantined_count, released_count, blocked, block_summary, report_json, created_at
        FROM inspect_batches WHERE id=?
        """,
        (batch_id,),
    ).fetchone()
    if not row:
        return None
    d = dict(row)
    d["report"] = json.loads(d.pop("report_json"))
    return d


def list_batch_rows(conn: sqlite3.Connection, batch_id: int) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT id, batch_id, row_no, entity_key, payload_json, disposition, release_state, rule_hits_json
        FROM inspect_rows WHERE batch_id=? ORDER BY row_no
        """,
        (batch_id,),
    ).fetchall()
    out = []
    for r in rows:
        d = dict(r)
        d["payload"] = json.loads(d.pop("payload_json"))
        d["rule_hits"] = json.loads(d.pop("rule_hits_json"))
        out.append(d)
    return out


def disposition_counts(conn: sqlite3.Connection, batch_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT disposition, COUNT(*) AS c FROM inspect_rows WHERE batch_id=? GROUP BY disposition",
        (batch_id,),
    ).fetchall()
    return {str(r["disposition"]): int(r["c"]) for r in rows}


def release_state_counts(conn: sqlite3.Connection, batch_id: int) -> dict[str, int]:
    rows = conn.execute(
        "SELECT release_state, COUNT(*) AS c FROM inspect_rows WHERE batch_id=? GROUP BY release_state",
        (batch_id,),
    ).fetchall()
    return {str(r["release_state"]): int(r["c"]) for r in rows}
