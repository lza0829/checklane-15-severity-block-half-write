import importlib

import pytest


@pytest.fixture()
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.db
    import app.seed
    import app.modules.severity_block_gate as gate
    import app.services.inspect_service as svc

    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.seed)
    importlib.reload(gate)
    importlib.reload(svc)
    app.seed.init_db()
    return svc


def test_orders_error_blocks_whole_batch(env):
    """有 error → 整批阻断：签发 0，pass 行也不得进精选区。"""
    detail = env.run_inspect(1)

    assert detail["scanned_count"] == 6
    # ORD-3 缺 sku、ORD-4 price 非数字 → 隔离；ORD-5 仅 warn
    assert detail["quarantined_count"] == 2
    assert detail["passed_count"] == 4
    assert detail["blocked"] == 1
    assert detail["status"] == "blocked"
    assert detail["released_count"] == 0
    # 阻断批无任何行新写入精选区（含本会 pass 的 ORD-1/2/5/6）
    assert detail["curated_total"] == 0
    assert detail["quarantine_total"] == 2

    gate = detail["report"]["gate"]
    assert gate["blocked"] is True
    assert gate["error_row_numbers"] == [3, 4]
    assert gate["released_allowed"] is False
    assert "因严重度阻断、签发为 0" in gate["summary"]
    assert detail["report"]["summary"] == gate["summary"]
    assert detail["report"]["counts"]["released"] == 0
    assert detail["report"]["counts"]["held"] == 4

    rows = {r["row_no"]: r for r in detail["rows"]}
    assert rows[3]["disposition"] == "quarantine"
    assert rows[4]["disposition"] == "quarantine"
    assert rows[5]["disposition"] == "pass"
    assert rows[5]["release_state"] == "held"
    assert all(
        rows[n]["release_state"] == "held" for n in (1, 2, 5, 6)
    )

    checks = {c["name"]: c for c in detail["report"]["checks"]}
    for name in (
        "scan_breakdown",
        "severity_gate_matches_error_hits",
        "persisted_batch_flag_matches",
        "release_matches_passed",
        "held_matches_blocked_passed",
        "curated_rows_for_batch",
        "quarantine_rows_for_batch",
        "row_dispositions_match_counts",
        "row_release_states_match",
    ):
        assert checks[name]["ok"] is True, (name, checks[name])

    # 隔离区留证，精选区为空
    from app.db import connect
    from app.repositories import curated, quarantine

    conn = connect()
    try:
        assert curated.count_all(conn) == 0
        assert quarantine.count_all(conn) == 2
    finally:
        conn.close()


def test_warn_only_does_not_block(env):
    """仅有 warn、没有 error → 不阻断，warn 行照原口径签发。"""
    detail = env.run_inspect(3)

    assert detail["scanned_count"] == 6
    assert detail["quarantined_count"] == 0
    assert detail["passed_count"] == 6
    assert detail["blocked"] == 0
    assert detail["status"] == "released"
    assert detail["released_count"] == 6
    assert detail["curated_total"] == 6

    gate = detail["report"]["gate"]
    assert gate["blocked"] is False
    assert gate["error_row_numbers"] == []
    assert detail["report"]["counts"]["held"] == 0
    assert all(r["release_state"] == "released" for r in detail["rows"])
    # 5 号行仍有 qty warn 命中，但照常放行
    row5 = next(r for r in detail["rows"] if r["row_no"] == 5)
    assert row5["disposition"] == "pass"
    assert [h["severity"] for h in row5["rule_hits"]] == ["warn"]


def test_block_then_fix_and_release_same_db(env):
    """同一本地库：先阻断零签发，再清因后签发对照，计数互证且不残留。"""
    blocked = env.run_inspect(1)
    assert blocked["blocked"] == 1 and blocked["released_count"] == 0

    released = env.run_inspect(3)
    assert released["blocked"] == 0 and released["released_count"] == 6

    from app.db import connect
    from app.repositories import batches, curated

    conn = connect()
    try:
        assert curated.count_by_batch(conn, blocked["id"]) == 0
        assert curated.count_by_batch(conn, released["id"]) == 6
        b1 = batches.get_batch(conn, blocked["id"])
        assert b1["status"] == "blocked" and b1["blocked"] == 1 and b1["released_count"] == 0
        b2 = batches.get_batch(conn, released["id"])
        assert b2["status"] == "released" and b2["blocked"] == 0 and b2["released_count"] == 6
        # 报告持久化，重开连接仍在
        assert "因严重度阻断、签发为 0" in b1["report"]["summary"]
    finally:
        conn.close()


def test_late_disable_of_error_rule_allows_release(env):
    """可复现方式：停用 error 规则后再跑同一 Orders 批 → 不再触发 error，可签发。"""
    from app.db import connect
    from app.repositories import rules

    conn = connect()
    try:
        for rule_id in (1, 2):
            rules.set_enabled(conn, rule_id, False)
        conn.commit()
    finally:
        conn.close()

    detail = env.run_inspect(1)
    assert detail["blocked"] == 0
    assert detail["released_count"] == 6 - detail["quarantined_count"]
    # qty 仅 warn → 仍进精选区
    assert detail["quarantined_count"] == 0
    assert detail["released_count"] == 6


def test_curated_delete_by_batch_only_affects_target_batch(env):
    """兜底清理只清目标批次误写的精选行，不碰其它批次既有签发。"""
    from app.db import connect
    from app.repositories import curated

    keep = env.run_inspect(3)
    blocked = env.run_inspect(1)
    conn = connect()
    try:
        duplicate_batch_id = blocked["id"]
        curated.upsert(
            conn,
            dataset_id=1,
            batch_id=duplicate_batch_id,
            entity_key="stray-1",
            payload={"entity_key": "stray-1"},
            released_at="2026-01-01T00:00:00Z",
        )
        conn.commit()
        assert curated.count_by_batch(conn, duplicate_batch_id) == 1
        assert curated.delete_by_batch(conn, duplicate_batch_id) == 1
        assert curated.count_by_batch(conn, duplicate_batch_id) == 0
        assert curated.count_by_batch(conn, keep["id"]) == 6
    finally:
        conn.close()


def test_init_db_migrates_legacy_schema(tmp_path, monkeypatch):
    """老库缺 blocked/release_state 列时启动自动补齐，旧数据仍可读。"""
    import sqlite3

    db_path = tmp_path / "app.db"
    conn = sqlite3.connect(db_path)
    conn.executescript(
        """
        CREATE TABLE inspect_batches(
          id INTEGER PRIMARY KEY, dataset_id INTEGER NOT NULL, status TEXT NOT NULL,
          scanned_count INTEGER NOT NULL DEFAULT 0, passed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0, quarantined_count INTEGER NOT NULL DEFAULT 0,
          released_count INTEGER NOT NULL DEFAULT 0,
          report_json TEXT NOT NULL, created_at TEXT NOT NULL
        );
        CREATE TABLE inspect_rows(
          id INTEGER PRIMARY KEY, batch_id INTEGER NOT NULL, row_no INTEGER NOT NULL,
          entity_key TEXT, payload_json TEXT NOT NULL, disposition TEXT NOT NULL,
          rule_hits_json TEXT NOT NULL
        );
        INSERT INTO inspect_batches(dataset_id,status,report_json,created_at)
          VALUES (1,'released','{}','2026-01-01T00:00:00Z');
        """
    )
    conn.commit()
    conn.close()

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    import app.config
    import app.db
    import app.seed

    importlib.reload(app.config)
    importlib.reload(app.db)
    importlib.reload(app.seed)
    app.seed.init_db()

    conn = app.db.connect()
    try:
        cols = {r["name"] for r in conn.execute("PRAGMA table_info(inspect_batches)")}
        assert {"blocked", "block_summary"} <= cols
        row_cols = {r["name"] for r in conn.execute("PRAGMA table_info(inspect_rows)")}
        assert "release_state" in row_cols
        old = conn.execute("SELECT blocked, block_summary FROM inspect_batches").fetchone()
        assert old["blocked"] == 0 and old["block_summary"] == ""
    finally:
        conn.close()
