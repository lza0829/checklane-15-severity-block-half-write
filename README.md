# 04-checklane（检道）

Checklane — 数据质检通道：入检批次装载 → 规则命中与严重度裁决 → 隔离/放行签发 → 签发清单与源批对账。

**不是** Feed 多源归并（Feedstitch），**不是** 作业依赖编排（Batchgate），**不是** CDC 变更流按序落库（Changeflow）。
**也不是** 报表看板 / CRUD 后台 / 电商订单 / OA 工单。

## 启动

```bash
docker compose up --build
```

| 入口 | 地址 |
| --- | --- |
| 前端 | http://localhost:5400 |
| API | http://localhost:10400 |

本地开发（可选）：

```bash
# backend
cd backend && pip install -r requirements.txt
DATA_DIR=../data uvicorn app.main:app --reload --port 10400

# frontend
cd frontend && npm install && npm run dev
```

## 主链

登记数据集与规则 → 装载夹具批次入检 → 按规则裁决放行/隔离 → 签发到精选区 → 批次报告与各列表数字同源

## 种子说明

- **Orders Snapshot**：JSONL 商品快照，含必填缺失与类型错误行，用于演示入检与隔离。
- **Inventory Snapshot**：库存快照，独立数据集，用于第二路规则演示。
- **Orders Snapshot（无 error 对照）**：同一 Orders 批次补全 sku/price，仅余 qty warn，用于「可签发」对照。

打开「入检台」选数据集跑一次，再看隔离区 / 精选区 / 批次报告计数是否与本次报告一致。

## 严重度整批阻断（已实现）

口径：**本批任一行出现 error 级命中 → 整批阻断签发，签发数为 0**。

- 一次入检先把全部行的规则命中算清，再做整批判决，最后统一落库；不存在边检边签发。
- 阻断批中本会 pass 的行一律 `held`，不写入精选区；error 行照常进隔离区留证。
- 仅有 warn、没有 error 时不做整批阻断，warn 行按原口径签发。
- 批次持久化 `blocked` / `block_summary`，每行持久化 `release_state`；报告含 `gate` 判决、
  「因严重度阻断、签发为 0」摘要与对账项（扫描/通过/隔离/签发/held、精选区与隔离区按批计数、
  行处置与落点汇总、库内标记回读）。
- 若阻断批曾误写精选区，落库前按 `batch_id` 兜底清除，确保零签发。

复现闭环：

1. 入检台跑 **Orders Snapshot**（数据集 1）→ 批次状态「已阻断签发」，签发 0，隔离 2。
2. 跑 **Orders Snapshot（无 error 对照）**（数据集 3）→ 同一批形状不再触发 error，签发 6。
   或在规则页停用数据集 1 的两条 error 规则再跑，效果相同。

## 测试

```bash
cd backend && python -m pytest
cd frontend && npx vitest run
```

## 技术栈

Python 3.12 + FastAPI + SQLite；Vue 3 + Vite + Nginx。

## 0-1 切口（未实现，见题单）

当前仓库已实现「单批入检 + 基础规则 + 放行签发 + 严重度整批阻断」主链。规则包版本、抽样入检、跨字段规则、批次重开、金标对照、批内重复键、放行冻结窗、规则临时豁免、跨数据集参照检查等仍在 `backend/app/modules/*` 留空包，按题单逐题换主链路实现。
