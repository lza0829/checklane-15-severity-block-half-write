import { describe, expect, it, vi, beforeEach } from 'vitest'
import { mount, flushPromises } from '@vue/test-utils'

const blockedBatch = {
  id: 1,
  dataset_id: 1,
  status: 'blocked',
  scanned_count: 6,
  passed_count: 4,
  quarantined_count: 2,
  released_count: 0,
  blocked: 1,
  block_summary: '因严重度阻断、签发为 0：本批 6 行中 2 行命中 error 级规则（行 3, 4）；整批均不写入精选区，error 行进隔离区。',
  report: {
    summary: '因严重度阻断、签发为 0：本批 6 行中 2 行命中 error 级规则（行 3, 4）；整批均不写入精选区，error 行进隔离区。',
    counts: { scanned: 6, passed: 4, failed: 2, quarantined: 2, released: 0, held: 4, blocked: true },
    gate: { blocked: true, error_row_numbers: [3, 4], error_rule_names: ['sku 必填', 'price 须为数字'], summary: '因严重度阻断、签发为 0' },
    checks: [{ name: 'scan_breakdown', ok: true, detail: 'scanned(6) = passed(4) + quarantined(2)' }],
  },
  rows: [
    { id: 1, row_no: 1, entity_key: 'ORD-1', disposition: 'pass', release_state: 'held', rule_hits: [] },
    { id: 2, row_no: 3, entity_key: 'ORD-3', disposition: 'quarantine', release_state: 'quarantined', rule_hits: [{ rule_id: 1, field: 'sku', name: 'sku 必填', severity: 'error' }] },
  ],
}

const fetchMock = vi.fn()
global.fetch = fetchMock

beforeEach(() => {
  fetchMock.mockReset()
})

function respond(urlMap) {
  fetchMock.mockImplementation(async (url) => ({
    ok: true,
    json: async () => urlMap[String(url)],
    text: async () => '',
  }))
}

async function mountWith(comp, urlMap) {
  respond(urlMap)
  const w = mount(comp)
  await flushPromises()
  return w
}

describe('blocked batch is visible in UI', () => {
  it('批次页列表与明细显示阻断状态与零签发', async () => {
    const Batches = (await import('../Batches.vue')).default
    const w = await mountWith(Batches, {
      '/api/batches': [blockedBatch],
      '/api/batches/1': blockedBatch,
    })
    expect(w.text()).toContain('已阻断签发')
    expect(w.text()).toContain('因严重度阻断、签发为 0')
    await w.findAll('tbody tr')[0].trigger('click')
    await flushPromises()
    expect(w.text()).toContain('held')
    expect(w.text()).toContain('严重度闸门')
  })

  it('入检台展示阻断状态、held 行与零签发', async () => {
    const Inspect = (await import('../Inspect.vue')).default
    const w = await mountWith(Inspect, { '/api/datasets': [{ id: 1, name: 'Orders Snapshot' }] })
    respond({ '/api/datasets': [{ id: 1, name: 'Orders Snapshot' }], '/api/inspect': blockedBatch })
    await w.find('button').trigger('click')
    await flushPromises()
    const text = w.text()
    expect(text).toContain('已阻断签发')
    expect(text).toContain('因严重度阻断、签发为 0')
    expect(text).toContain('阻断未签发')
    expect(text).toContain('sku 必填·error')
  })

  it('规则页区分 error 与 warn', async () => {
    const Rules = (await import('../Rules.vue')).default
    const w = await mountWith(Rules, {
      '/api/rules': [
        { id: 1, dataset_id: 1, name: 'sku 必填', field: 'sku', rule_type: 'required', severity: 'error', enabled: 1 },
        { id: 3, dataset_id: 1, name: 'qty 须为整数', field: 'qty', rule_type: 'type_int', severity: 'warn', enabled: 1 },
      ],
    })
    const tags = w.findAll('.tag')
    const errorTag = tags.find((t) => t.text() === 'error')
    const warnTag = tags.find((t) => t.text() === 'warn')
    expect(errorTag.classes()).toContain('danger')
    expect(warnTag.classes()).toContain('warn')
  })
})
