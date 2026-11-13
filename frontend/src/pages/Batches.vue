<script setup>
import { onMounted, ref } from 'vue'
import { getJSON } from '../api'
const rows = ref([])
const detail = ref(null)
const err = ref('')
async function load() {
  rows.value = await getJSON('/api/batches')
}
async function show(id) {
  detail.value = await getJSON(`/api/batches/${id}`)
}
onMounted(async () => {
  try { await load() }
  catch (e) { err.value = String(e.message || e) }
})
</script>
<template>
  <div class="page">
    <header>
      <h1>批次</h1>
      <p class="muted">历史入检批次与报告</p>
    </header>
    <p v-if="err" class="tag danger">{{ err }}</p>
    <div class="panel">
      <table>
        <thead><tr><th>ID</th><th>数据集</th><th>状态</th><th>扫描</th><th>通过</th><th>隔离</th><th>签发</th><th>摘要</th><th>时间</th></tr></thead>
        <tbody>
          <tr v-for="r in rows" :key="r.id" style="cursor:pointer" @click="show(r.id)">
            <td>{{ r.id }}</td>
            <td>{{ r.dataset_id }}</td>
            <td>
              <span class="tag" :class="r.blocked ? 'danger' : (r.status === 'released' ? 'ok' : '')">
                {{ r.blocked ? '已阻断签发' : r.status }}
              </span>
            </td>
            <td>{{ r.scanned_count }}</td>
            <td>{{ r.passed_count }}</td>
            <td>{{ r.quarantined_count }}</td>
            <td>{{ r.released_count }}</td>
            <td class="muted blockcell">{{ r.block_summary }}</td>
            <td>{{ r.created_at }}</td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="panel" v-if="detail">
      <h3>
        批次 #{{ detail.id }} 明细
        <span class="tag" :class="detail.blocked ? 'danger' : (detail.status === 'released' ? 'ok' : '')">
          {{ detail.blocked ? '已阻断签发' : detail.status }}
        </span>
      </h3>
      <p v-if="detail.blocked" class="alert-block">{{ detail.block_summary }}</p>
      <div class="tiles">
        <div class="tile"><div class="hero-num">{{ detail.scanned_count }}</div><div class="muted">扫描</div></div>
        <div class="tile"><div class="hero-num">{{ detail.passed_count }}</div><div class="muted">通过</div></div>
        <div class="tile"><div class="hero-num">{{ detail.quarantined_count }}</div><div class="muted">隔离</div></div>
        <div class="tile"><div class="hero-num" :class="{ dangernum: detail.blocked }">{{ detail.released_count }}</div><div class="muted">签发</div></div>
      </div>
      <h4>严重度闸门</h4>
      <p v-if="detail.report.gate" class="muted">
        {{ detail.report.gate.summary }}
        （error 行：{{ detail.report.gate.error_row_numbers.join('、') || '—' }}）
      </p>
      <p v-else class="muted">该批次产生于严重度闸门上线前，无闸门判决记录。</p>
      <h4>行清单</h4>
      <table>
        <thead><tr><th>#</th><th>键</th><th>处置</th><th>落点</th><th>命中</th></tr></thead>
        <tbody>
          <tr v-for="r in detail.rows" :key="r.id">
            <td>{{ r.row_no }}</td>
            <td>{{ r.entity_key }}</td>
            <td>{{ r.disposition }}</td>
            <td><span class="tag" :class="r.release_state === 'held' ? 'warn' : (r.release_state === 'quarantined' ? 'danger' : 'ok')">{{ r.release_state }}</span></td>
            <td class="muted">{{ r.rule_hits.map(h => h.name + '·' + h.severity).join('；') || '—' }}</td>
          </tr>
        </tbody>
      </table>
      <h4>报告原文</h4>
      <pre class="muted" style="white-space:pre-wrap">{{ JSON.stringify(detail.report, null, 2) }}</pre>
    </div>
  </div>
</template>
