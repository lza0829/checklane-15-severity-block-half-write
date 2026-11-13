from datetime import datetime, timezone

from app.config import DATA_DIR
from app.db import connect


def _now() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat().replace("+00:00", "Z")


_MIGRATIONS = {
    "inspect_batches": {
        "blocked": "INTEGER NOT NULL DEFAULT 0",
        "block_summary": "TEXT NOT NULL DEFAULT ''",
    },
    "inspect_rows": {
        "release_state": "TEXT NOT NULL DEFAULT 'released'",
    },
}


def _migrate(conn) -> None:
    """老库补列：CREATE TABLE IF NOT EXISTS 不会给既有表加列。"""
    for table, columns in _MIGRATIONS.items():
        existing = {row["name"] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}
        for name, ddl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {ddl}")


def init_db() -> None:
    DATA_DIR.mkdir(parents=True, exist_ok=True)
    conn = connect()
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS settings(
          key TEXT PRIMARY KEY,
          value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS datasets(
          id INTEGER PRIMARY KEY,
          name TEXT NOT NULL,
          fixture_path TEXT NOT NULL,
          entity_type TEXT NOT NULL,
          enabled INTEGER NOT NULL DEFAULT 1,
          note TEXT
        );
        CREATE TABLE IF NOT EXISTS rules(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          name TEXT NOT NULL,
          field TEXT NOT NULL,
          rule_type TEXT NOT NULL,
          severity TEXT NOT NULL,
          params_json TEXT NOT NULL DEFAULT '{}',
          enabled INTEGER NOT NULL DEFAULT 1,
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS inspect_batches(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          status TEXT NOT NULL,
          scanned_count INTEGER NOT NULL DEFAULT 0,
          passed_count INTEGER NOT NULL DEFAULT 0,
          failed_count INTEGER NOT NULL DEFAULT 0,
          quarantined_count INTEGER NOT NULL DEFAULT 0,
          released_count INTEGER NOT NULL DEFAULT 0,
          blocked INTEGER NOT NULL DEFAULT 0,
          block_summary TEXT NOT NULL DEFAULT '',
          report_json TEXT NOT NULL,
          created_at TEXT NOT NULL,
          FOREIGN KEY(dataset_id) REFERENCES datasets(id)
        );
        CREATE TABLE IF NOT EXISTS inspect_rows(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          payload_json TEXT NOT NULL,
          disposition TEXT NOT NULL,
          release_state TEXT NOT NULL DEFAULT 'released',
          rule_hits_json TEXT NOT NULL,
          FOREIGN KEY(batch_id) REFERENCES inspect_batches(id)
        );
        CREATE TABLE IF NOT EXISTS quarantine(
          id INTEGER PRIMARY KEY,
          batch_id INTEGER NOT NULL,
          dataset_id INTEGER NOT NULL,
          row_no INTEGER NOT NULL,
          entity_key TEXT,
          reason TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          created_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS curated(
          id INTEGER PRIMARY KEY,
          dataset_id INTEGER NOT NULL,
          batch_id INTEGER NOT NULL,
          entity_key TEXT NOT NULL,
          payload_json TEXT NOT NULL,
          released_at TEXT NOT NULL,
          UNIQUE(dataset_id, entity_key)
        );
        """
    )
    _migrate(conn)
    n = conn.execute("SELECT COUNT(*) AS c FROM datasets").fetchone()["c"]
    if n == 0:
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            ("Orders Snapshot", "orders_snapshot.jsonl", "order", 1, "含缺失与类型错误行，演示入检"),
        )
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            ("Inventory Snapshot", "inventory_snapshot.jsonl", "sku", 1, "库存快照第二路"),
        )
        conn.execute(
            "INSERT INTO datasets(name, fixture_path, entity_type, enabled, note) VALUES (?,?,?,?,?)",
            (
                "Orders Snapshot（无 error 对照）",
                "orders_clean_snapshot.jsonl",
                "order",
                1,
                "同一 Orders 批次补全 sku/price，仅余 qty warn，用于可签发对照",
            ),
        )
        conn.executemany(
            "INSERT INTO rules(dataset_id, name, field, rule_type, severity, params_json, enabled) VALUES (?,?,?,?,?,?,?)",
            [
                (1, "sku 必填", "sku", "required", "error", "{}", 1),
                (1, "price 须为数字", "price", "type_number", "error", "{}", 1),
                (1, "qty 须为整数", "qty", "type_int", "warn", "{}", 1),
                (2, "sku 必填", "sku", "required", "error", "{}", 1),
                (2, "on_hand 须为整数", "on_hand", "type_int", "error", "{}", 1),
                (3, "sku 必填", "sku", "required", "error", "{}", 1),
                (3, "price 须为数字", "price", "type_number", "error", "{}", 1),
                (3, "qty 须为整数", "qty", "type_int", "warn", "{}", 1),
            ],
        )
        conn.execute(
            "INSERT INTO settings(key, value) VALUES (?, ?)",
            ("release_mode", "pass_only"),
        )
    conn.commit()
    conn.close()
