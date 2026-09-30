<template>
  <section class="page" data-module="coldchain">
    <header class="page-head">
      <div>
        <h2>冷藏箱温度报警处置</h2>
        <p class="page-desc">
          报警单沿「待处理 → 处理中 → 已调温 → 已断电」逐跳留痕；同一插电桩反复报警自动归并，
          设定温度只取冷机读数，越档了结一律退回处理中。
        </p>
      </div>
      <div class="page-actions">
        <button class="btn primary" type="button" @click="openAlarmForm()">提交温度报警</button>
      </div>
    </header>

    <!-- 处置看板：计数与监控明细同源实时重算 -->
    <div class="stat-row">
      <article class="stat-card">
        <span class="stat-label">未完结报警单</span>
        <strong class="stat-value">{{ board['未完结'] ?? 0 }}</strong>
      </article>
      <article v-for="item in boardCards" :key="item.label" class="stat-card">
        <span class="stat-label">{{ item.label }}</span>
        <strong class="stat-value" :class="item.cls">{{ item.value }}</strong>
      </article>
      <article class="stat-card">
        <span class="stat-label">重复报警归并（只认第一次）</span>
        <strong class="stat-value">{{ board['重复报警归并次数'] ?? 0 }}</strong>
      </article>
    </div>

    <!-- 报警单 -->
    <h3 class="section-title">报警单记录链 <small>按插电桩归并，每一次状态流转都可复盘</small></h3>

    <form class="filter-bar" @submit.prevent="reloadAlarms">
      <label class="filter-item">
        <span>报警单号 / 冷藏箱号</span>
        <input v-model="keyword" placeholder="按单号或箱号检索" />
      </label>
      <label class="filter-item">
        <span>插电桩号</span>
        <input v-model="plugNo" placeholder="按插电桩检索" />
      </label>
      <button class="btn" type="submit">查询</button>
      <button class="btn ghost" type="button" @click="resetFilters">重置</button>
    </form>

    <div class="chip-row">
      <button
        v-for="chip in statusChips"
        :key="chip.key"
        class="chip"
        :class="{ active: statusFilter === chip.key }"
        type="button"
        @click="toggleStatus(chip.key)"
      >
        {{ chip.label }}<span class="n">{{ chip.count }}</span>
      </button>
    </div>

    <table class="data-table">
      <thead>
        <tr>
          <th>报警单号</th>
          <th>冷藏箱号</th>
          <th>插电桩号</th>
          <th>报警方向</th>
          <th>归并次数</th>
          <th>状态</th>
          <th>设定温度</th>
          <th>当前温度</th>
          <th>当前处理人</th>
          <th>上一位处理人</th>
          <th>最近报警</th>
          <th>记录链</th>
          <th>处置动作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in alarms" :key="String(row.id)">
          <td>{{ row['报警单号'] }}</td>
          <td>{{ row['冷藏箱号'] }}</td>
          <td>{{ row['插电桩号'] }}</td>
          <td>{{ row['报警方向'] }}</td>
          <td>{{ row['报警次数'] }} 次</td>
          <td><span class="badge" :class="`badge-${row.status}`">{{ row.status }}</span></td>
          <td>{{ row['设定温度'] ?? '—' }}</td>
          <td>{{ row['当前温度'] ?? '—' }}</td>
          <td>{{ row['当前处理人'] ?? '未认领' }}</td>
          <td>{{ row['上一位处理人'] ?? '—' }}</td>
          <td>{{ row['最近报警时间'] }}</td>
          <td><button class="link" type="button" @click="openTimeline(row)">事件链（{{ row.events?.length ?? 0 }}）</button></td>
          <td>
            <div class="row-actions">
              <button
                v-for="act in availableActions(row.status)"
                :key="act.key"
                class="link"
                type="button"
                @click="openAction(act, row)"
              >
                {{ act.label }}
              </button>
            </div>
          </td>
        </tr>
        <tr v-if="!alarms.length">
          <td colspan="13" class="empty-state">当前筛选下没有报警单</td>
        </tr>
      </tbody>
    </table>
    <footer class="page-foot">
      <span>共 {{ alarmTotal }} 条报警单（未完结计数随明细实时重算）</span>
      <span v-if="message" :class="messageOk ? 'ok-text' : 'error-text'">{{ message }}</span>
    </footer>

    <!-- 监控明细（冷机读数） -->
    <h3 class="section-title">冷藏箱监控明细 <small>设定温度取自冷机读数；读数取不到的插电桩禁止调温归档</small></h3>
    <table class="data-table">
      <thead>
        <tr>
          <th>冷藏箱号</th>
          <th>插电桩号</th>
          <th>监控状态</th>
          <th>设定温度</th>
          <th>当前温度</th>
          <th>运行电流</th>
          <th>温度偏差</th>
          <th>冷机读数</th>
          <th>操作</th>
        </tr>
      </thead>
      <tbody>
        <tr v-for="row in monitors" :key="String(row.id)">
          <td>{{ row['冷藏箱号'] }}</td>
          <td>{{ row['插电桩号'] }}</td>
          <td>{{ row['监控状态'] }}</td>
          <td>{{ row['设定温度'] }}</td>
          <td>{{ row['当前温度'] }}</td>
          <td>{{ row['运行电流'] }}</td>
          <td>{{ row['温度偏差'] }}</td>
          <td>
            <span v-if="readingOnline(row['插电桩号'])" class="badge">读数正常 {{ readingAt(row['插电桩号']) }}</span>
            <span v-else class="badge badge-offline">读数取不到</span>
          </td>
          <td>
            <button class="link" type="button" @click="openAlarmForm(row)">就此箱报警</button>
          </td>
        </tr>
        <tr v-if="!monitors.length">
          <td colspan="9" class="empty-state">暂无冷藏箱监控明细</td>
        </tr>
      </tbody>
    </table>

    <!-- 提交报警弹窗 -->
    <div v-if="alarmForm" class="modal-mask" @click.self="alarmForm = null">
      <div class="modal">
        <h3>提交温度报警</h3>
        <div class="form-grid">
          <label>
            <span class="required">冷藏箱号</span>
            <input v-model="alarmForm['冷藏箱号']" placeholder="如 CBHU2836152" />
          </label>
          <label>
            <span class="required">插电桩号</span>
            <input v-model="alarmForm['插电桩号']" placeholder="如 P-1001，归并口径" />
          </label>
          <label>
            <span class="required">报警方向</span>
            <select v-model="alarmForm['报警方向']">
              <option value="高温报警">高温报警</option>
              <option value="低温报警">低温报警</option>
            </select>
          </label>
          <label>
            <span>当前温度（现场核对）</span>
            <input v-model="alarmForm['当前温度']" placeholder="如 -12.6" />
          </label>
          <label class="full">
            <span>温度偏差 / 补充说明</span>
            <textarea v-model="alarmForm.remark" rows="2" placeholder="现场观察、箱况、读数异常等"></textarea>
          </label>
          <label class="full">
            <span>提交人</span>
            <input v-model="alarmForm.operator" />
          </label>
        </div>
        <p class="page-desc" style="margin-top:8px">
          同一插电桩已有未完结报警单时，本次提交会自动归并到首单并累加次数，不会另开新单。
        </p>
        <div class="modal-foot">
          <button class="btn ghost" type="button" @click="alarmForm = null">取消</button>
          <button class="btn primary" type="button" @click="submitAlarm">提交</button>
        </div>
      </div>
    </div>

    <!-- 处置动作弹窗 -->
    <div v-if="actionForm" class="modal-mask" @click.self="actionForm = null">
      <div class="modal">
        <h3>{{ actionForm.title }} · {{ actionForm.ticket['报警单号'] }}</h3>
        <div class="kv-note">
          冷藏箱 {{ actionForm.ticket['冷藏箱号'] }} ｜ 插电桩 {{ actionForm.ticket['插电桩号'] }} ｜
          当前状态 <span class="badge" :class="`badge-${actionForm.ticket.status}`">{{ actionForm.ticket.status }}</span>
        </div>
        <div v-if="actionForm.key === '完成调温'" class="kv-note">
          设定温度不接受手填，仅取冷机读数。当前插电桩冷机：
          <span v-if="actionForm.reading" class="ok-text">
            设定 {{ actionForm.reading.setpoint }}℃ ｜ 箱温 {{ actionForm.reading.current_temp }}℃ ｜ {{ actionForm.reading.read_at }}
          </span>
          <span v-else class="error-text">读数取不到（离线 / 设定温度帧缺失），提交后后端也会拒绝归档</span>
        </div>
        <div class="form-grid">
          <label class="full">
            <span>{{ actionForm.key === '断电处置' ? '断电处置说明（必填）' : '处置说明' }}</span>
            <textarea
              v-model="actionForm.note"
              rows="4"
              :placeholder="actionForm.key === '断电处置'
                ? '断电原因、现场安排、通知箱主/箱管情况……'
                : '可留空；越档拦截、退回处理中等操作会在此记录原因'"
            ></textarea>
          </label>
          <label class="full">
            <span>处理人（换班后仍记录上一位处理人）</span>
            <input v-model="actionForm.operator" />
          </label>
        </div>
        <div class="modal-foot">
          <button class="btn ghost" type="button" @click="actionForm = null">取消</button>
          <button class="btn primary" type="button" @click="submitAction">确认{{ actionForm.title }}</button>
        </div>
      </div>
    </div>

    <!-- 事件链弹窗 -->
    <div v-if="timelineTicket" class="modal-mask" @click.self="timelineTicket = null">
      <div class="modal">
        <h3>记录链 · {{ timelineTicket['报警单号'] }}</h3>
        <div class="kv-note">
          {{ timelineTicket['冷藏箱号'] }} ｜ 插电桩 {{ timelineTicket['插电桩号'] }} ｜
          报警 {{ timelineTicket['报警次数'] }} 次 ｜ 首位报警人：{{ timelineTicket['首位报警人'] }}
        </div>
        <div v-if="timelineTicket['断电说明']" class="kv-note">
          <strong>断电说明：</strong>{{ timelineTicket['断电说明'] }}
        </div>
        <ol class="timeline">
          <li v-for="ev in [...(timelineTicket.events ?? [])].reverse()" :key="ev.seq">
            <div>
              <strong>{{ ev.action }}</strong>
              <span class="badge" :class="`badge-${ev.to_status}`" style="margin-left:6px">
                {{ ev.from_status ?? '—' }} → {{ ev.to_status }}
              </span>
            </div>
            <div class="tl-time">{{ ev.time }} ｜ 处理人 <span class="tl-op">{{ ev.operator }}</span></div>
            <div v-if="ev.note">{{ ev.note }}</div>
          </li>
        </ol>
        <div class="modal-foot">
          <button class="btn primary" type="button" @click="timelineTicket = null">关闭</button>
        </div>
      </div>
    </div>
  </section>
</template>

<script setup lang="ts">
import { computed, onMounted, reactive, ref } from 'vue'

import { request } from '@/api/client'
import { useSessionStore } from '@/stores/session'

const ENDPOINT = '/api/coldchain'
const session = useSessionStore()

type Row = Record<string, any>
type Board = Record<string, number>
type Reading = { online: boolean; setpoint: string | null; current_temp: string | null; current_amp: string | null; read_at: string }

const STATUSES = ['待处理', '处理中', '已调温', '已断电']

const alarms = ref<Row[]>([])
const alarmTotal = ref(0)
const monitors = ref<Row[]>([])
const readings = ref<Record<string, Reading>>({})
const board = ref<Board>({})

const keyword = ref('')
const plugNo = ref('')
const statusFilter = ref<string>('')
const message = ref('')
const messageOk = ref(true)

const alarmForm = ref<Row | null>(null)
const timelineTicket = ref<Row | null>(null)
const actionForm = ref<(Row & { ticket: Row; title: string; reading: Reading | null }) | null>(null)

const boardCards = computed(() => [
  { label: '待处理', value: board.value['待处理'] ?? 0, cls: 'error-text' },
  { label: '处理中', value: board.value['处理中'] ?? 0, cls: '' },
  { label: '已调温（待了结）', value: board.value['已调温'] ?? 0, cls: '' },
  { label: '已断电', value: board.value['已断电'] ?? 0, cls: '' },
])

const statusChips = computed(() =>
  STATUSES.map((label) => ({
    key: label,
    label,
    count: board.value[label] ?? 0,
  })),
)

function flash(msg: string, ok = true) {
  message.value = msg
  messageOk.value = ok
}

function toggleStatus(key: string) {
  statusFilter.value = statusFilter.value === key ? '' : key
  void reloadAlarms()
}

function resetFilters() {
  keyword.value = ''
  plugNo.value = ''
  statusFilter.value = ''
  void reloadAlarms()
}

// 按当前状态给出可执行动作；越档动作也允许点，由后端拦截并退回处理中（现场需要看到为什么不能了结）
function availableActions(status: string) {
  const all = [
    { key: '开始处理', label: '开始处理' },
    { key: '完成调温', label: '完成调温' },
    { key: '断电处置', label: '断电了结' },
    { key: '退回处理中', label: '退回处理中' },
  ]
  if (status === '待处理') return [all[0], all[2]]
  if (status === '处理中') return [all[1], all[2]]
  if (status === '已调温') return [all[2], all[3]]
  return [all[3]]
}

function readingOnline(plug: string) {
  const r = readings.value[plug]
  return Boolean(r && r.online && r.setpoint)
}
function readingAt(plug: string) {
  return readings.value[plug]?.read_at ?? ''
}

function openAlarmForm(row?: Row) {
  alarmForm.value = reactive({
    '冷藏箱号': row?.['冷藏箱号'] ?? '',
    '插电桩号': row?.['插电桩号'] ?? '',
    '报警方向': '高温报警',
    '当前温度': row && row['当前温度'] !== '—' ? row['当前温度'] : '',
    remark: '',
    operator: session.operator,
  })
}

async function submitAlarm() {
  const values = { ...alarmForm.value }
  const resp = await request(`${ENDPOINT}/alarms`, { method: 'POST', body: JSON.stringify({ values }) })
  const data = await resp.json()
  if (!resp.ok || !data.ok) {
    flash(data.message || data.detail || '报警提交失败', false)
    return
  }
  alarmForm.value = null
  flash(data.message, true)
  await reloadAll()
}

function openTimeline(row: Row) {
  // 列表行里已带 events，但为看到最新流转结果，拉一次详情
  void request(`${ENDPOINT}/alarms/${row.id}`)
    .then((r) => r.json())
    .then((detail: Row) => {
      timelineTicket.value = detail
    })
    .catch(() => {
      timelineTicket.value = row
    })
}

async function openAction(act: { key: string; label: string }, ticket: Row) {
  let reading: Reading | null = null
  if (act.key === '完成调温') {
    reading = readings.value[ticket['插电桩号']] ?? null
    if (reading && (!reading.online || !reading.setpoint)) reading = null
  }
  actionForm.value = reactive({
    key: act.key,
    title: act.label,
    ticket,
    note: '',
    operator: session.operator,
    reading,
  })
}

async function submitAction() {
  if (!actionForm.value) return
  const form = actionForm.value
  if (form.key === '断电处置' && !form.note.trim()) {
    flash('断电了结必须填写处置说明', false)
    return
  }
  const resp = await request(`${ENDPOINT}/alarms/${form.ticket.id}/actions`, {
    method: 'POST',
    body: JSON.stringify({ values: { action: form.key, operator: form.operator, note: form.note } }),
  })
  const data = await resp.json()
  if (!resp.ok || !data.ok) {
    // 越档拦截、读数取不到等：后端已退回处理中并留痕，关掉弹窗刷新看板
    flash(data.message || data.detail || '动作未生效', false)
    actionForm.value = null
    await reloadAll()
    return
  }
  actionForm.value = null
  flash(data.message, true)
  await reloadAll()
}

async function reloadAlarms() {
  const query = new URLSearchParams()
  if (keyword.value.trim()) query.set('keyword', keyword.value.trim())
  if (plugNo.value.trim()) query.set('plug_no', plugNo.value.trim())
  if (statusFilter.value) query.set('status', statusFilter.value)
  const resp = await request(`${ENDPOINT}/alarms?${query.toString()}`)
  if (!resp.ok) {
    flash('报警单列表读取失败', false)
    return
  }
  const data = await resp.json()
  alarms.value = data.items ?? []
  alarmTotal.value = data.total ?? 0
}

async function reloadBoard() {
  const resp = await request(`${ENDPOINT}/alarms/board`)
  if (resp.ok) board.value = await resp.json()
}

async function reloadMonitors() {
  const [listResp, readResp] = await Promise.all([
    request(`${ENDPOINT}?size=200`),
    request(`${ENDPOINT}/readings`),
  ])
  if (listResp.ok) {
    const data = await listResp.json()
    monitors.value = data.items ?? []
  }
  if (readResp.ok) {
    const data = await readResp.json()
    readings.value = data.items ?? {}
  }
}

async function reloadAll() {
  await Promise.all([reloadAlarms(), reloadBoard()])
}

onMounted(async () => {
  await Promise.all([reloadAlarms(), reloadBoard(), reloadMonitors()])
})
</script>
