from __future__ import annotations

import json
from typing import Any

from app.config import FIXTURES_DIR
from app.db import connect
from app.engines.parse_fixture import parse_jsonl
from app.engines.rule_engine import decide_disposition, evaluate_row
from app.modules.severity_block_gate import evaluate_gate, release_state_for
from app.repositories import batches as batch_repo
from app.repositories import curated as curated_repo
from app.repositories import datasets as dataset_repo
from app.repositories import quarantine as quarantine_repo
from app.repositories import rules as rules_repo
from app.seed import _now


def _filled(counts: dict[str, int], keys: tuple[str, ...]) -> dict[str, int]:
    """把 GROUP BY 缺省的零计数补齐，便于与报告口径逐键比对。"""
    return {k: int(counts.get(k, 0)) for k in keys}


def run_inspect(dataset_id: int) -> dict[str, Any]:
    """一次入检：全量算清行命中 → 整批判决 → 统一落库签发/隔离。

    先算清再写，任何写入都发生在整批判决之后；阻断批不写精选区，
    并对本批次任何残留的精选行做兜底清理，确保零签发。
    """
    conn = connect()
    try:
        ds = dataset_repo.get_dataset(conn, dataset_id)
        if not ds:
            raise ValueError(f"dataset {dataset_id} not found")
        if not ds["enabled"]:
            raise ValueError(f"dataset {dataset_id} disabled")

        path = FIXTURES_DIR / ds["fixture_path"]
        rows = parse_jsonl(path)
        rules = [r for r in rules_repo.list_rules(conn, dataset_id) if r.get("enabled")]

        scanned = len(rows)
        now = _now()

        # 阶段一：只计算，不落任何库（禁止边检边签发）
        row_results: list[dict[str, Any]] = []
        for idx, payload in enumerate(rows, start=1):
            hits = evaluate_row(payload, rules)
            row_results.append(
                {
                    "row_no": idx,
                    "entity_key": str(payload.get("entity_key") or "") or None,
                    "payload": payload,
                    "disposition": decide_disposition(hits),
                    "rule_hits": hits,
                }
            )

        # 阶段二：整批闸门 —— 任一行 error 命中即整批阻断
        gate = evaluate_gate(row_results)
        blocked = bool(gate["blocked"])

        passed = sum(1 for r in row_results if r["disposition"] == "pass")
        quarantined = sum(1 for r in row_results if r["disposition"] == "quarantine")
        failed = quarantined
        # released 由下面的实际写入累加：阻断批一条都不写，天然为 0
        released = 0
        held = passed if blocked else 0

        for item in row_results:
            item["release_state"] = release_state_for(item["disposition"], gate)

        status = "blocked" if blocked else ("released" if passed else "inspected")
        summary = gate["summary"]

        report = {
            "dataset_id": dataset_id,
            "dataset_name": ds["name"],
            "fixture_path": ds["fixture_path"],
            "rule_count": len(rules),
            "counts": {
                "scanned": scanned,
                "passed": passed,
                "failed": failed,
                "quarantined": quarantined,
                "released": released,
                "held": held,
                "blocked": blocked,
            },
            "gate": gate,
            "summary": summary,
            "checks": [],
        }

        # 阶段三：先建批次壳（此时尚无任何精选/隔离行），再统一写入。
        # 入检前精选存量快照：阻断批结束后必须与快照逐行一致，证明无新增也无覆盖。
        curated_before = curated_repo.count_all(conn, dataset_id)
        batch_id = batch_repo.insert_batch(
            conn,
            dataset_id=dataset_id,
            status=status,
            scanned=scanned,
            passed=passed,
            failed=failed,
            quarantined=quarantined,
            released=0,
            blocked=blocked,
            block_summary=summary,
            report=report,
            created_at=now,
        )

        for item in row_results:
            batch_repo.insert_row(
                conn,
                batch_id=batch_id,
                row_no=item["row_no"],
                entity_key=item["entity_key"],
                payload=item["payload"],
                disposition=item["disposition"],
                release_state=item["release_state"],
                rule_hits=item["rule_hits"],
            )
            if item["disposition"] == "quarantine":
                reasons = "; ".join(
                    f"{h['name']}:{h['detail']}" for h in item["rule_hits"] if h.get("severity") == "error"
                )
                quarantine_repo.insert(
                    conn,
                    batch_id=batch_id,
                    dataset_id=dataset_id,
                    row_no=item["row_no"],
                    entity_key=item["entity_key"],
                    reason=reasons or "rule hit",
                    payload=item["payload"],
                    created_at=now,
                )
            elif item["disposition"] == "pass":
                if blocked:
                    # 阻断批：pass 行一律 held，禁止任何精选区写入（含 upsert 覆盖），
                    # 计数自然保持 0；残留半写入在循环后按 batch_id 兜底清理。
                    continue
                key = item["entity_key"] or f"row-{item['row_no']}"
                curated_repo.upsert(
                    conn,
                    dataset_id=dataset_id,
                    batch_id=batch_id,
                    entity_key=key,
                    payload=item["payload"],
                    released_at=now,
                )
                released += 1

        if blocked:
            # 兜底清理本批任何半写入精选行（upsert 抢占/历史残留），
            # 只删 batch_id 命中的行，不碰其它批次既有签发；
            # 被本批 upsert 覆盖的他批同键行由“阻断即不 upsert”从源头避免。
            curated_repo.delete_by_batch(conn, batch_id)

        # 阶段四：用库内实际行数回填报告，互证后落盘
        curated_after = curated_repo.count_all(conn, dataset_id)

        counts = {
            "scanned": scanned,
            "passed": passed,
            "failed": failed,
            "quarantined": quarantined,
            "released": released,
            "held": held,
            "blocked": blocked,
        }
        report["counts"] = counts
        report["checks"] = [
            {
                "name": "scan_breakdown",
                "ok": scanned == passed + quarantined,
                "detail": f"scanned({scanned}) = passed({passed}) + quarantined({quarantined})",
            },
            {
                "name": "severity_gate_matches_error_hits",
                "ok": blocked == any(
                    h.get("severity") == "error" for r in row_results for h in r["rule_hits"]
                ),
                "detail": (
                    f"blocked={blocked}，error 行 {gate['error_row_count']}；" + summary
                ),
            },
            {
                "name": "release_matches_passed",
                "ok": released == (0 if blocked else passed),
                "detail": (
                    f"blocked 批 released=0，passed={passed} 行全部 held" if blocked
                    else f"released({released}) = passed({passed})"
                ),
            },
            {
                "name": "held_matches_blocked_passed",
                "ok": held == (passed if blocked else 0),
                "detail": f"held({held}) = blocked ? passed({passed}) : 0",
            },
            {
                "name": "curated_rows_for_batch",
                "ok": curated_repo.count_by_batch(conn, batch_id) == released,
                "detail": (
                    f"curated(batch_id={batch_id})="
                    f"{curated_repo.count_by_batch(conn, batch_id)} = released({released})"
                ),
            },
            {
                "name": "curated_no_delta_when_blocked",
                "ok": (not blocked) or (curated_repo.count_by_batch(conn, batch_id) == 0),
                "detail": (
                    f"阻断批精选区零本批增量：curated(batch_id={batch_id})=0，"
                    f"数据集精选存量入检前后 {curated_before} → {curated_after}（含半写入兜底清理）"
                    if blocked
                    else f"非阻断批正常签发：curated {curated_before} → {curated_after}"
                ),
            },
            {
                "name": "quarantine_rows_for_batch",
                "ok": quarantine_repo.count_by_batch(conn, batch_id) == quarantined,
                "detail": (
                    f"quarantine(batch_id={batch_id})="
                    f"{quarantine_repo.count_by_batch(conn, batch_id)} = quarantined({quarantined})"
                ),
            },
            {
                "name": "row_dispositions_match_counts",
                "ok": _filled(batch_repo.disposition_counts(conn, batch_id), ("pass", "quarantine"))
                == _filled({"pass": passed, "quarantine": quarantined}, ("pass", "quarantine")),
                "detail": f"inspect_rows 处置汇总 = passed({passed}) + quarantined({quarantined})",
            },
            {
                "name": "row_release_states_match",
                "ok": _filled(
                    batch_repo.release_state_counts(conn, batch_id),
                    ("released", "held", "quarantined"),
                ) == _filled(
                    {"released": released, "held": held, "quarantined": quarantined},
                    ("released", "held", "quarantined"),
                ),
                "detail": (
                    f"release_state 汇总：held({held}) released({released}) quarantined({quarantined})"
                ),
            },
        ]
        report["rows"] = [
            {
                "row_no": r["row_no"],
                "entity_key": r["entity_key"],
                "disposition": r["disposition"],
                "release_state": r["release_state"],
            }
            for r in row_results
        ]

        batch_repo.finalize_batch(
            conn,
            batch_id,
            status=status,
            released=released,
            blocked=blocked,
            block_summary=summary,
            report=report,
        )
        # 落盘后回读库内标记，报告与库状态互证
        persisted = conn.execute(
            "SELECT blocked, released_count FROM inspect_batches WHERE id=?", (batch_id,)
        ).fetchone()
        report["checks"].append(
            {
                "name": "persisted_batch_flag_matches",
                "ok": bool(persisted["blocked"]) == blocked and persisted["released_count"] == released,
                "detail": (
                    f"db blocked={bool(persisted['blocked'])} released={persisted['released_count']}；"
                    f"报告 blocked={blocked} released={released}"
                ),
            }
        )
        conn.execute(
            "UPDATE inspect_batches SET report_json=? WHERE id=?",
            (json.dumps(report, ensure_ascii=False), batch_id),
        )
        conn.commit()

        detail = batch_repo.get_batch(conn, batch_id)
        assert detail is not None
        detail["rows"] = batch_repo.list_batch_rows(conn, batch_id)
        detail["curated_total"] = curated_repo.count_all(conn, dataset_id)
        detail["quarantine_total"] = quarantine_repo.count_all(conn)
        return detail
    finally:
        conn.close()


def dashboard_stats() -> dict[str, Any]:
    conn = connect()
    try:
        datasets = conn.execute("SELECT COUNT(*) AS c FROM datasets").fetchone()["c"]
        rules = conn.execute("SELECT COUNT(*) AS c FROM rules WHERE enabled=1").fetchone()["c"]
        batches = conn.execute("SELECT COUNT(*) AS c FROM inspect_batches").fetchone()["c"]
        blocked = conn.execute(
            "SELECT COUNT(*) AS c FROM inspect_batches WHERE blocked=1"
        ).fetchone()["c"]
        latest_blocked = conn.execute(
            "SELECT id, dataset_id, block_summary, created_at FROM inspect_batches "
            "WHERE blocked=1 ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return {
            "dataset_count": datasets,
            "enabled_rule_count": rules,
            "batch_count": batches,
            "curated_count": curated_repo.count_all(conn),
            "quarantine_count": quarantine_repo.count_all(conn),
            "blocked_batch_count": blocked,
            "latest_blocked_batch": dict(latest_blocked) if latest_blocked else None,
        }
    finally:
        conn.close()
