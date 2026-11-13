"""severity_block_gate —— 严重度整批阻断闸门。

业务口径：
- 本批任意一行出现 error 级命中 → 整批阻断签发，签发数 0；本批任何行
  （包括规则判定为 pass 的行）都不得新写入精选区，error 行照常进隔离区留证。
- 仅有 warn 命中、没有 error → 不做整批阻断，warn 行仍按单行口径放行。

闸门在「一次入检内先算清全部行命中」之后、任何签发写入之前裁决，
避免边检边签发把半批写进精选区。
"""

from __future__ import annotations

from typing import Any

ERROR_SEVERITY = "error"
BLOCK_RULE = "any_error_blocks_batch"
GATE_NAME = "severity_block_gate"

# 单行签发落点
RELEASE_STATE_RELEASED = "released"
RELEASE_STATE_HELD = "held"
RELEASE_STATE_QUARANTINED = "quarantined"


def row_error_hits(rule_hits: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """行命中中的 error 级子集。"""
    return [h for h in rule_hits if h.get("severity") == ERROR_SEVERITY]


def evaluate_gate(row_results: list[dict[str, Any]]) -> dict[str, Any]:
    """对本批全部行的规则命中做整批判决（不读写数据库）。

    ``row_results`` 每项至少包含 ``row_no`` 与 ``rule_hits``。
    """
    error_row_numbers: list[int | None] = []
    error_rule_names: set[str] = set()
    error_hit_count = 0
    for item in row_results:
        hits = row_error_hits(item.get("rule_hits") or [])
        if not hits:
            continue
        error_row_numbers.append(item.get("row_no"))
        error_hit_count += len(hits)
        error_rule_names.update(str(h.get("name") or "") for h in hits)

    blocked = bool(error_row_numbers)
    if blocked:
        rows_text = ", ".join(str(n) for n in error_row_numbers)
        summary = (
            f"因严重度阻断、签发为 0：本批 {len(row_results)} 行中 {len(error_row_numbers)} 行"
            f"命中 error 级规则（行 {rows_text}）；整批均不写入精选区，error 行进隔离区。"
        )
    else:
        summary = (
            f"本批 {len(row_results)} 行无 error 级命中，允许按行签发；"
            "warn 命中不触发整批阻断。"
        )

    return {
        "name": GATE_NAME,
        "rule": BLOCK_RULE,
        "blocked": blocked,
        "severity": ERROR_SEVERITY,
        "error_row_numbers": error_row_numbers,
        "error_row_count": len(error_row_numbers),
        "error_hit_count": error_hit_count,
        "error_rule_names": sorted(n for n in error_rule_names if n),
        "released_allowed": not blocked,
        "summary": summary,
    }


def release_state_for(disposition: str, gate: dict[str, Any]) -> str:
    """单行落点：隔离行留证；阻断批的通过行 held（不签发）；否则 released。"""
    if disposition == "quarantine":
        return RELEASE_STATE_QUARANTINED
    if gate.get("blocked"):
        return RELEASE_STATE_HELD
    return RELEASE_STATE_RELEASED
