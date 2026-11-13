<script setup>
import { onMounted, ref } from 'vue'
import { getJSON, postJSON } from '../api'
const datasets = ref([])
const datasetId = ref(1)
const busy = ref(false)
const err = ref('')
const last = ref(null)
onMounted(async () => {
  datasets.value = await getJSON('/api/datasets')
  if (datasets.value.length) datasetId.value = datasets.value[0].id
})
async function run() {
  busy.value = true
  err.value = ''
  try {
    last.value = await postJSON('/api/inspect', { dataset_id: Number(datasetId.value) })
  } catch (e) {
    err.value = String(e.message || e)
  } finally {
    busy.value = false
  }
}
function stateTag(r) {
  if (r.release_state === 'quarantined') return 'danger'
  if (r.release_state === 'held') return 'warn'
  return 'ok'
}
function stateText(r) {
  if (r.release_state === 'quarantined') return '隔离'
  if (r.release_state === 'held') return '阻断未签发'
  return '已签发'
}
</script>
<template>
  <div class="page">
    <header>
      <h1>入检台</h1>
      <p class="muted">装载夹具 → 全量执行规则 → 整批严重度阻断裁决 → 隔离/签发</p>
    </header>
    <div class="panel row">
      <label>数据集
        <select v-model="datasetId">
          <option v-for="d in datasets" :key="d.id" :value="d.id">{{ d.id }} · {{ d.name }}</option>
        </select>
      </label>
      <button :disabled="busy" @click="run">{{ busy ? '入检中…' : '开始入检' }}</button>
    </div>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel" v-if="last">
      <h3>
        批次 #{{ last.id }}
        <span class="tag" :class="last.blocked ? 'danger' : (last.status === 'released' ? 'ok' : '')">
          {{ last.blocked ? '已阻断签发' : last.status }}
        </span>
      </h3>
      <p v-if="last.blocked" class="alert-block">{{ last.report.summary }}</p>
      <div class="tiles">
        <div class="tile"><div class="hero-num">{{ last.scanned_count }}</div><div class="muted">扫描</div></div>
        <div class="tile"><div class="hero-num">{{ last.passed_count }}</div><div class="muted">通过</div></div>
        <div class="tile"><div class="hero-num">{{ last.quarantined_count }}</div><div class="muted">隔离</div></div>
        <div class="tile"><div class="hero-num" :class="{ dangernum: last.blocked }">{{ last.released_count }}</div><div class="muted">签发</div></div>
        <div class="tile" v-if="last.blocked"><div class="hero-num">{{ last.report.counts.held }}</div><div class="muted">阻断未签发</div></div>
      </div>
      <p v-if="last.blocked" class="muted">
        error 行：{{ last.report.gate.error_row_numbers.join('、') || '—' }}；
        error 规则：{{ last.report.gate.error_rule_names.join('、') || '—' }}
      </p>
      <h4>对账</h4>
      <table>
        <thead><tr><th>校验</th><th>结果</th><th>说明</th></tr></thead>
        <tbody>
          <tr v-for="c in last.report.checks" :key="c.name">
            <td>{{ c.name }}</td>
            <td><span class="tag" :class="c.ok ? 'ok' : 'danger'">{{ c.ok ? '通过' : '失败' }}</span></td>
            <td class="muted">{{ c.detail }}</td>
          </tr>
        </tbody>
      </table>
      <h4>行清单</h4>
      <table>
        <thead><tr><th>#</th><th>键</th><th>处置</th><th>落点</th><th>命中</th></tr></thead>
        <tbody>
          <tr v-for="r in last.rows" :key="r.id">
            <td>{{ r.row_no }}</td>
            <td>{{ r.entity_key }}</td>
            <td><span class="tag" :class="r.disposition === 'pass' ? 'ok' : 'danger'">{{ r.disposition }}</span></td>
            <td><span class="tag" :class="stateTag(r)">{{ stateText(r) }}</span></td>
            <td class="muted">
              <span v-for="h in r.rule_hits" :key="h.rule_id + '-' + h.field" class="tag"
                    :class="h.severity === 'error' ? 'danger' : 'warn'">{{ h.name }}·{{ h.severity }}</span>
              <template v-if="!r.rule_hits.length">—</template>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>
</template>
