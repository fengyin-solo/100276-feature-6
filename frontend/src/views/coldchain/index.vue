<template>
  <section class="page" data-module="coldchain">
    <header class="page-head">
      <div>
        <h2>冷藏箱温度报警处置</h2>
        <p class="page-desc">
          报警单沿「待处理 → 处理中 → 已调温 → 已断电」流转；同箱同桩重复报警按插电桩归并，设定温度只取冷机读数，每步都留下处理人与工班可复盘。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openReport">上报温度报警</button>
        <button class="btn" type="button" @click="exportRows">导出未完结报警单</button>
      </div>
    </header>

    <!-- 当班人：换班后处理人变了，但每张单的历史里仍留着上一位处理人 -->
    <div class="duty-bar">
      <label class="filter-item">
        <span>当前处理人</span>
        <input v-model="operator" placeholder="处理人姓名" />
      </label>
      <label class="filter-item">
        <span>当前工班</span>
        <select v-model="shiftLabel">
          <option>白班 08:00-20:00</option>
          <option>夜班 20:00-08:00</option>
        </select>
      </label>
      <span class="duty-hint">操作将以该处理人/工班写入记录链；换班只改这里，不动历史</span>
      <span class="board-time">看板重算于 {{ board.统计时间 || '—' }}</span>
    </div>

    <div class="stat-row">
      <article v-for="card in boardCards" :key="card.label" class="stat-card" :class="card.cls">
        <span class="stat-label">{{ card.label }}</span>
        <strong class="stat-value">{{ card.value }}</strong>
      </article>
    </div>

    <form class="filter-bar" @submit.prevent="reload">
      <label class="filter-item">
        <span>冷藏箱号 / 报警单号</span>
        <input v-model="filters.keyword" placeholder="按箱号或单号检索" />
      </label>
      <label class="filter-item">
        <span>插电桩号</span>
        <input v-model="filters.plug_no" placeholder="按插电桩检索" />
      </label>
      <label class="filter-item">
        <span>状态</span>
        <select v-model="filters.status">
          <option value="">全部状态</option>
          <option v-for="s in statuses" :key="s" :value="s">{{ s }}</option>
        </select>
      </label>
      <label class="filter-item check">
        <input v-model="includeArchived" type="checkbox" />
        <span>包含已归档</span>
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置条件</button>
    </form>

    <table class="data-table">
      <thead>
        <tr>
          <th>报警单号</th>
          <th>冷藏箱号</th>
          <th>插电桩号</th>
          <th>状态</th>
          <th>报警次数</th>
          <th>报警/读数温度(℃)</th>
          <th>设定温度(℃)</th>
          <th>首报人(工班)</th>
          <th>当前处理人(工班)</th>
          <th>断电说明</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="String(row.id)" :class="{ archived: row.归档 }">
          <td>{{ row.报警单号 }}</td>
          <td>{{ row.冷藏箱号 }}</td>
          <td>{{ row.插电桩号 }}</td>
          <td><span class="status-tag" :data-status="row.status">{{ row.status }}</span></td>
          <td>
            {{ row.报警次数 }}
            <em v-if="row.报警次数 > 1" class="merged-hint">已归并重复 {{ row.报警次数 - 1 }} 次</em>
          </td>
          <td>{{ formatTemp(row.报警温度) }}</td>
          <td>{{ formatTemp(row.设定温度) }}</td>
          <td>{{ row.首报人 }}<br /><small>{{ row.首报工班 }}</small></td>
          <td>
            <template v-if="row.当前处理人">{{ row.当前处理人 }}<br /><small>{{ row.当前工班 }}</small></template>
            <span v-else class="muted">尚未接单</span>
          </td>
          <td class="note-cell">{{ row.断电说明 || '—' }}</td>
          <td class="row-actions vertical">
            <button class="link" type="button" @click="runAction('接单处理', row)" v-if="row.status === '待处理'">接单处理</button>
            <button class="link" type="button" @click="runAction('调整设定温度', row)" v-if="row.status === '处理中'">
              调整设定温度（取冷机读数）
            </button>
            <button class="link danger" type="button" @click="openPowerOff(row)" v-if="row.status === '已调温'">
              断电处置
            </button>
            <button class="link" type="button" @click="runAction('断电处置', row)" v-if="row.status === '处理中'">
              直接断电（越档演示）
            </button>
            <button class="link" type="button" @click="archiveRow(row)" v-if="!row.归档 && isTerminal(row.status)">归档</button>
            <button class="link" type="button" @click="openHistory(row)">记录链 {{ (row.history || []).length }} 步</button>
          </td>
        </tr>
        <tr v-if="!rows.length">
          <td colspan="11" class="empty-state">暂无未完结报警单，可先「上报温度报警」</td>
        </tr>
      </tbody>
    </table>

    <footer class="page-foot">
      <span>共 {{ total }} 条报警单</span>
      <span v-if="notice" :class="noticeOk ? 'ok-text' : 'error-text'">{{ notice }}</span>
    </footer>

    <!-- 上报报警 -->
    <div v-if="modal === 'report'" class="modal-mask" @click.self="closeModal">
      <div class="modal">
        <h3>上报温度报警</h3>
        <p class="page-desc">同一只冷藏箱在同一插电桩的重复报警会归并到第一张单，只认第一次上报。</p>
        <label class="form-item"><span>冷藏箱号</span>
          <input v-model="reportForm.冷藏箱号" placeholder="如 RCU-2201" @blur="previewReading" />
        </label>
        <label class="form-item"><span>插电桩号</span>
          <input v-model="reportForm.插电桩号" placeholder="如 P-07" />
        </label>
        <div v-if="readingPreview" class="reading-box" :class="readingPreview.ok ? 'ok' : 'fail'">
          <template v-if="readingPreview.ok">
            冷机读数：设定 {{ readingPreview.设定温度 }}℃ ／ 当前 {{ readingPreview.当前温度 }}℃（{{ readingPreview.时间 }}）
          </template>
          <template v-else>冷机读数取不到：{{ readingPreview.message }}（仍可上报，处置时会受限）</template>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" type="button" @click="closeModal">取消</button>
          <button class="btn primary" type="button" @click="submitReport">提交报警</button>
        </div>
      </div>
    </div>

    <!-- 断电说明 -->
    <div v-if="modal === 'poweroff'" class="modal-mask" @click.self="closeModal">
      <div class="modal">
        <h3>断电处置：{{ activeRow?.报警单号 }}</h3>
        <p class="page-desc">断电是最终了结，必须留下处置说明；处置后报警单落到「已断电」。</p>
        <label class="form-item"><span>处置说明（必填）</span>
          <textarea v-model="powerNote" rows="4" placeholder="如：已拔除 P-07 电源并挂牌，箱温异常疑似冷机故障，转修箱班组"></textarea>
        </label>
        <div class="modal-foot">
          <button class="btn ghost" type="button" @click="closeModal">取消</button>
          <button class="btn primary danger-btn" type="button" @click="confirmPowerOff">确认断电了结</button>
        </div>
      </div>
    </div>

    <!-- 记录链 -->
    <div v-if="modal === 'history'" class="modal-mask wide" @click.self="closeModal">
      <div class="modal">
        <h3>报警记录链：{{ activeRow?.报警单号 }}</h3>
        <p class="page-desc">
          {{ activeRow?.冷藏箱号 }} · 插电桩 {{ activeRow?.插电桩号 }} · 首报 {{ activeRow?.首报人 }}（{{ activeRow?.首报工班 }}），
          归并重复报警 {{ Math.max((activeRow?.报警次数 || 1) - 1, 0) }} 次
        </p>
        <table class="data-table history-table">
          <thead>
            <tr><th>#</th><th>时间</th><th>动作</th><th>原状态</th><th>新状态</th><th>处理人</th><th>工班</th><th>说明</th></tr>
          </thead>
          <tbody>
            <tr v-for="h in activeRow?.history || []" :key="h.序号" :class="{ merged: h.归并 }">
              <td>{{ h.序号 }}</td>
              <td>{{ h.时间 }}</td>
              <td>{{ h.动作 }}<em v-if="h.归并" class="merged-hint">（归并）</em></td>
              <td>{{ h.原状态 }}</td>
              <td>{{ h.新状态 }}</td>
              <td>{{ h.处理人 }}</td>
              <td>{{ h.工班 }}</td>
              <td>{{ h.说明 }}</td>
            </tr>
          </tbody>
        </table>
        <div class="modal-foot">
          <button class="btn" type="button" @click="closeModal">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

type HistoryItem = {
  序号: number
  时间: string
  动作: string
  原状态: string
  新状态: string
  处理人: string
  工班: string
  说明: string
  归并?: boolean
}

type Row = {
  id: number
  报警单号: string
  冷藏箱号: string
  插电桩号: string
  status: string
  报警次数: number
  报警温度: number | null
  设定温度: number | null
  首报人: string
  首报工班: string
  当前处理人: string | null
  当前工班: string | null
  断电说明: string | null
  归档: boolean
  history?: HistoryItem[]
}

type BoardCard = { label: string; value: number }
type Board = {
  cards: BoardCard[]
  未完结: number
  已归并: number
  已归档: number
  统计时间: string
}

type Reading = { ok: boolean; message?: string; 设定温度?: number; 当前温度?: number; 时间?: string }

type Result = { ok: boolean; message: string; code?: string; entry?: Row | null }

const ENDPOINT = '/api/coldchain'
const statuses = ['待处理', '处理中', '已调温', '已断电']
const session = useSessionStore()

const rows = ref<Row[]>([])
const total = ref(0)
const includeArchived = ref(false)
const filters = reactive({ keyword: '', plug_no: '', status: '' })
const notice = ref('')
const noticeOk = ref(false)

const board = ref<Board>({ cards: [], 未完结: 0, 已归并: 0, 已归档: 0, 统计时间: '' })
const operator = ref(session.operator)
const shiftLabel = ref(session.shiftLabel)

const modal = ref<'' | 'report' | 'poweroff' | 'history'>('')
const activeRow = ref<Row | null>(null)
const reportForm = reactive({ 冷藏箱号: '', 插电桩号: '' })
const readingPreview = ref<Reading | null>(null)
const powerNote = ref('')

const boardCards = computed(() => {
  const cls = (label: string) => (label === '未完结报警单' ? 'strong' : '')
  return board.value.cards.map((c) => ({ ...c, cls: cls(c.label) }))
})

function isTerminal(status: string) {
  return status === '已调温' || status === '已断电'
}

function formatTemp(value: number | null) {
  return value === null || value === undefined ? '—' : String(value)
}

function flash(message: string, ok: boolean) {
  notice.value = message
  noticeOk.value = ok
}

function resetFilters() {
  filters.keyword = ''
  filters.plug_no = ''
  filters.status = ''
  includeArchived.value = false
  void reload()
}

function exportRows() {
  window.open(`${ENDPOINT}/export`, '_blank')
}

async function call(path: string, body: Record<string, unknown>): Promise<Result> {
  const response = await request(path, { method: 'POST', body: JSON.stringify(body) })
  return (await response.json()) as Result
}

function withDuty(extra: Record<string, unknown>) {
  session.setOperator(operator.value)
  session.setShift(shiftLabel.value)
  return { 处理人: operator.value, 工班: shiftLabel.value, ...extra }
}

async function reload() {
  notice.value = ''
  const query = new URLSearchParams()
  if (filters.keyword) query.set('keyword', filters.keyword)
  if (filters.plug_no) query.set('plug_no', filters.plug_no)
  if (filters.status) query.set('status', filters.status)
  if (includeArchived.value) query.set('include_archived', 'true')
  try {
    const response = await request(`${ENDPOINT}?${query.toString()}`)
    if (!response.ok) throw new Error('报警单列表读取失败')
    const payload = (await response.json()) as { items: Row[]; total: number }
    rows.value = payload.items ?? []
    total.value = payload.total ?? rows.value.length
    // 处置看板与监控明细同一拍重算，避免看板条数和列表对不上。
    await reloadBoard()
  } catch (error) {
    flash(error instanceof Error ? error.message : '列表读取失败', false)
  }
}

async function reloadBoard() {
  const response = await request(`${ENDPOINT}/board`)
  if (response.ok) board.value = (await response.json()) as Board
}

async function runAction(action: string, row: Row) {
  try {
    const result = await call(`${ENDPOINT}/${row.id}/actions`, withDuty({ action }))
    flash(result.message, result.ok)
    // 越档拦截也会把状态退回处理中，仍需刷新明细与看板。
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '动作提交失败', false)
  }
}

async function archiveRow(row: Row) {
  try {
    const result = await call(`${ENDPOINT}/${row.id}/archive`, withDuty({}))
    flash(result.message, result.ok)
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '归档失败', false)
  }
}

function openReport() {
  reportForm.冷藏箱号 = ''
  reportForm.插电桩号 = ''
  readingPreview.value = null
  modal.value = 'report'
}

function openPowerOff(row: Row) {
  activeRow.value = row
  powerNote.value = ''
  modal.value = 'poweroff'
}

function openHistory(row: Row) {
  activeRow.value = row
  modal.value = 'history'
}

function closeModal() {
  modal.value = ''
  activeRow.value = null
}

async function previewReading() {
  if (!reportForm.冷藏箱号.trim()) {
    readingPreview.value = null
    return
  }
  const response = await request(`${ENDPOINT}/readings/${encodeURIComponent(reportForm.冷藏箱号.trim())}`)
  if (response.ok) readingPreview.value = (await response.json()) as Reading
}

async function submitReport() {
  if (!reportForm.冷藏箱号.trim() || !reportForm.插电桩号.trim()) {
    flash('冷藏箱号和插电桩号都必填', false)
    return
  }
  try {
    const result = await call(
      `${ENDPOINT}/alarms`,
      withDuty({ 冷藏箱号: reportForm.冷藏箱号.trim(), 插电桩号: reportForm.插电桩号.trim() }),
    )
    flash(result.message, result.ok)
    closeModal()
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '报警提交失败', false)
  }
}

async function confirmPowerOff() {
  if (!activeRow.value) return
  if (!powerNote.value.trim()) {
    flash('断电处置必须填写处置说明', false)
    return
  }
  try {
    const result = await call(
      `${ENDPOINT}/${activeRow.value.id}/actions`,
      withDuty({ action: '断电处置', 说明: powerNote.value.trim() }),
    )
    flash(result.message, result.ok)
    closeModal()
    await reload()
  } catch (error) {
    flash(error instanceof Error ? error.message : '断电处置失败', false)
  }
}

onMounted(reload)
</script>

<style scoped>
.duty-bar {
  display: flex;
  gap: 12px;
  align-items: flex-end;
  background: #fff;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 10px 12px;
  margin-bottom: 12px;
}
.duty-bar .filter-item span { display: block; font-size: 12px; color: var(--muted); }
.duty-bar input,
.duty-bar select { padding: 4px 8px; }
.duty-hint { color: var(--muted); font-size: 12px; }
.board-time { margin-left: auto; color: var(--muted); font-size: 12px; }
.stat-card.strong { border-color: var(--brand); background: #f0f6ff; }
.filter-item.check { display: flex; align-items: center; gap: 4px; }
.filter-item.check span { color: #1f2937; }
.status-tag { padding: 2px 8px; border-radius: 10px; font-size: 12px; background: #eef2f7; }
.status-tag[data-status='待处理'] { background: #fee4e2; color: #b42318; }
.status-tag[data-status='处理中'] { background: #fef0c7; color: #b54708; }
.status-tag[data-status='已调温'] { background: #d1fadf; color: #027a48; }
.status-tag[data-status='已断电'] { background: #e4e7ec; color: #475467; }
.merged-hint { color: #b54708; font-style: normal; font-size: 11px; margin-left: 4px; }
.muted { color: var(--muted); }
.note-cell { max-width: 220px; }
.row-actions.vertical { flex-direction: column; align-items: flex-start; gap: 6px; }
.link.danger,
.error-text { color: #b42318; }
.ok-text { color: #027a48; }
tr.archived td { color: var(--muted); background: #fafbfc; }
.modal-mask {
  position: fixed;
  inset: 0;
  background: rgba(16, 24, 40, 0.45);
  display: flex;
  align-items: center;
  justify-content: center;
  z-index: 50;
}
.modal {
  width: 460px;
  background: #fff;
  border-radius: 10px;
  padding: 18px 20px;
}
.modal-mask.wide .modal { width: 860px; }
.modal h3 { margin: 0 0 8px; }
.form-item { display: block; margin: 10px 0; }
.form-item span { display: block; font-size: 12px; color: var(--muted); margin-bottom: 4px; }
.form-item input,
.form-item textarea { width: 100%; padding: 6px 8px; border: 1px solid var(--border); border-radius: 6px; }
.reading-box { border-radius: 6px; padding: 8px 10px; font-size: 13px; margin: 8px 0; }
.reading-box.ok { background: #ecfdf3; color: #027a48; }
.reading-box.fail { background: #fef3f2; color: #b42318; }
.modal-foot { display: flex; justify-content: flex-end; gap: 8px; margin-top: 12px; }
.danger-btn { background: #b42318; border-color: #b42318; color: #fff; }
.history-table { font-size: 12px; }
.history-table tr.merged td { background: #fffaeb; }
</style>
