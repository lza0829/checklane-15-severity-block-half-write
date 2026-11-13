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


def test_blocked_batch_leaves_preexisting_curated_untouched(env):
    """入检前精选区已有存量（含与本批 pass 行同键的行）：阻断后存量逐行不变，
    无新增也无覆盖，批次态 blocked、签发 0 三者同时成立。"""
    from app.db import connect
    from app.repositories import curated, quarantine

    conn = connect()
    try:
        # 既有精选：ORD-1 与夹具内 pass 行同键（验证阻断不得 upsert 覆盖），
        # EXTRA-9 是本批完全不涉及的存量（验证不得误删）。
        curated.upsert(
            conn,
            dataset_id=1,
            batch_id=999,
            entity_key="ORD-1",
            payload={"entity_key": "ORD-1", "title": "既有版本-不得被覆盖"},
            released_at="2026-01-01T00:00:00Z",
        )
        curated.upsert(
            conn,
            dataset_id=1,
            batch_id=999,
            entity_key="EXTRA-9",
            payload={"entity_key": "EXTRA-9"},
            released_at="2026-01-01T00:00:00Z",
        )
        conn.commit()
        before = {r["entity_key"]: r for r in curated.list_all(conn, dataset_id=1)}
        assert set(before) == {"ORD-1", "EXTRA-9"}
    finally:
        conn.close()

    detail = env.run_inspect(1)

    # 阻断态 + 签发 0 + 精选无本批增量
    assert detail["blocked"] == 1
    assert detail["status"] == "blocked"
    assert detail["released_count"] == 0
    assert detail["curated_total"] == len(before)

    conn = connect()
    try:
        after = {r["entity_key"]: r for r in curated.list_all(conn, dataset_id=1)}
        # 入检前后精选存量逐行一致：键集合、payload、来源 batch_id 都未变
        assert set(after) == set(before)
        for key, row in before.items():
            assert after[key]["payload"] == row["payload"]
            assert after[key]["batch_id"] == row["batch_id"] == 999
        # 本批在精选区零留痕
        assert curated.count_by_batch(conn, detail["id"]) == 0
        # 隔离区 error 行照常留证
        assert quarantine.count_by_batch(conn, detail["id"]) == 2
        assert quarantine.count_all(conn) == 2
    finally:
        conn.close()

    checks = {c["name"]: c for c in detail["report"]["checks"]}
    assert checks["curated_no_delta_when_blocked"]["ok"] is True
    assert "零本批增量" in checks["curated_no_delta_when_blocked"]["detail"]
    assert "2 → 2" in checks["curated_no_delta_when_blocked"]["detail"]
    assert checks["curated_rows_for_batch"]["ok"] is True


def test_blocked_batch_sweeps_half_written_curated_rows(env):
    """阻断路径上若已存在本批 batch_id 的半写入精选行，入检结束前必须清掉，
    使精选区相对入检前零增量。"""
    from app.db import connect
    from app.repositories import curated

    # 先跑一个正常批次留存量，确保兜底清理不波及他批
    keep = env.run_inspect(3)
    assert keep["released_count"] == 6

    conn = connect()
    try:
        before_total = curated.count_all(conn)
        # 预先拿到下一个批次 id 并植入“半写入”残留（模拟写入先于阻断裁定）
        next_id = conn.execute("SELECT COALESCE(MAX(id), 0) + 1 AS n FROM inspect_batches").fetchone()["n"]
        curated.upsert(
            conn,
            dataset_id=1,
            batch_id=next_id,
            entity_key="HALF-WRITTEN-1",
            payload={"entity_key": "HALF-WRITTEN-1"},
            released_at="2026-01-01T00:00:00Z",
        )
        conn.commit()
        assert curated.count_by_batch(conn, next_id) == 1
    finally:
        conn.close()

    detail = env.run_inspect(1)
    assert detail["id"] == next_id
    assert detail["blocked"] == 1 and detail["released_count"] == 0

    conn = connect()
    try:
        # 半写入已清；他批既有签发原样保留；精选总量回到入检前
        assert curated.count_by_batch(conn, next_id) == 0
        assert curated.count_all(conn) == before_total
        assert curated.count_by_batch(conn, keep["id"]) == 6
        assert all(r["entity_key"] != "HALF-WRITTEN-1" for r in curated.list_all(conn, dataset_id=1))
    finally:
        conn.close()

    checks = {c["name"]: c for c in detail["report"]["checks"]}
    assert checks["curated_no_delta_when_blocked"]["ok"] is True


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
