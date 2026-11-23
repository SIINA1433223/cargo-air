<template>
  <div class="wd-wrap">
    <header class="wd-head">
      <h2>业务工作台</h2>
    </header>

    <div v-if="isDashboard" class="dash">
      <h3>{{ stats.title || roleLabel + '仪表盘' }}</h3>
      <div class="nums">
        <div v-for="(label, key) in stats.labels" :key="key" class="num">
          <span class="v">{{ stats.values?.[key] ?? 0 }}</span>
          <span class="l">{{ label }}</span>
        </div>
      </div>
    </div>
    <div v-else-if="error" class="err">{{ error }}</div>
    <table v-else class="wd-table">
      <thead>
        <tr><th v-for="c in columns" :key="c.col">{{ c.label }}</th></tr>
      </thead>
      <tbody>
        <tr v-for="row in rows" :key="row.id">
          <td v-for="c in columns" :key="c.col">{{ show(row.fields[c.col]?.value) }}</td>
        </tr>
      </tbody>
    </table>
    <p class="hint">共 {{ total }} 条 · 第 {{ page }} 页</p>
  </div>
</template>

<script setup lang="ts">
  import { computed, onMounted, ref, watch } from 'vue'
  import { useRoute } from 'vue-router'
  import { COLUMNS } from './columns'
  import request from '@/utils/http'

  defineOptions({ name: 'WeidaWorkbench' })

  const TABS = [
    { role: 'customer_service', label: '国内客服' },
    { role: 'pricing', label: '航线价格' },
    { role: 'domestic_ops', label: '国内操作' },
  ]
  const route = useRoute()
  // 菜单里每个部门是独立路径（/weida/customer-service），路由参数或末段路径都能认出来
  const role = computed(() => {
    // 菜单路径用连字符（customer-service），接口用下划线（customer_service），这里统一
    const seg = String(route.params.role || route.path.split('/').filter(Boolean).pop() || '')
      .replace(/-/g, '_')
    return ['customer_service', 'pricing', 'domestic_ops'].includes(seg) ? seg : 'customer_service'
  })
  const isDashboard = computed(() => (route.path || '').includes('dashboard'))
  const roleLabel = computed(() => ({ customer_service: '国内客服', pricing: '航线价格', domestic_ops: '国内操作' } as Record<string, string>)[role.value] || '')
  const stats = ref<any>({})
  const DASH_ROLE: Record<string, string> = {
    'dashboard-cs': 'customer_service',
    'dashboard-pricing': 'pricing',
    'dashboard-ops': 'domestic_ops',
  }
  async function loadStats() {
    const seg = (route.path.split('/').filter(Boolean).pop() || '').replace(/-/g, '_')
    const key = Object.keys(DASH_ROLE).find((k) => k.replace(/-/g, '_') === seg)
    const target = key ? DASH_ROLE[key] : role.value
    try {
      const res = await request<any>({ url: `/weida/${target}/stats`, method: 'get' })
      stats.value = res.data?.data ?? {}
    } catch {
      stats.value = {}
    }
  }
  const rows = ref<any[]>([])
  const total = ref(0)
  const page = ref(1)
  const error = ref('')

  // 列表太宽，只挑几张关键列先显示（后续按配置给完整列）
  const PREFERRED = ['客户 REF NO.', '商机编号', '订单编号', '货物品名', '客户', '负责航线', '单票状态', '航线状态']

  const columns = computed(() => {
    const sample = rows.value[0]?.fields || {}
    const wanted = COLUMNS[role.value] || []
    const out: Array<{ col: string; label: string }> = []
    // 列名比较**去掉所有空格**再比：妙搭里有「目的 港」这种带空格的写法
    const norm = (s: string) => String(s || '').replace(/\s+/g, '')
    for (const label of wanted) {
      const target = norm(label)
      const key = Object.keys(sample).find((k) => norm(sample[k]?.label || k) === target)
      if (key) out.push({ col: key, label: sample[key]?.label || label })
    }
    // 配置里点名的列一个都没匹配上时，退回前 12 个字段，避免整屏空白
    if (out.length === 0) {
      return Object.keys(sample)
        .filter((k) => k !== 'feishu_record_id')
        .slice(0, 12)
        .map((k) => ({ col: k, label: sample[k].label || k }))
    }
    return out
  })

  function show(v: any): string {
    if (v === null || v === undefined) return ''
    if (Array.isArray(v)) return v.map(String).join(', ')
    if (typeof v === 'object') return JSON.stringify(v)
    return String(v)
  }

  async function load() {
    error.value = ''
    try {
      const res = await request<any>({
        url: `/weida/${role.value}/list`,
        method: 'get',
        params: { page: page.value, page_size: 50 },
      })
      const data = res.data?.data ?? {}
      rows.value = data.items || []
      total.value = data.total || 0
    } catch (e: any) {
      error.value = e?.message || '加载失败'
    }
  }

  watch(role, () => { page.value = 1; load() })
  onMounted(() => { if (isDashboard.value) loadStats(); else load() })
  watch(() => route.path, () => { if (isDashboard.value) loadStats(); else { page.value = 1; load() } })
</script>

<style scoped>
  .wd-wrap { padding: 20px; background: #f7f4ee; min-height: 100%; color: #2b2a28; }
  .wd-head { display: flex; align-items: baseline; gap: 16px; margin-bottom: 14px; }
  .wd-head h2 { font-size: 18px; color: #1f4b45; margin: 0; }
  .tabs button { margin-right: 8px; padding: 5px 12px; border: 1px solid #ddd6c9; background: #fffdf9;
    border-radius: 6px; cursor: pointer; font-size: 13px; color: #6b655c; }
  .tabs button.on { background: #1f4b45; border-color: #1f4b45; color: #f7f4ee; }
  .wd-table { width: 100%; border-collapse: collapse; background: #fffdf9; font-size: 13px; }
  .wd-table th, .wd-table td { border-bottom: 1px solid #e8e2d6; padding: 8px 10px; text-align: left;
    white-space: nowrap; overflow: hidden; text-overflow: ellipsis; max-width: 220px; }
  .wd-table th { background: #f1ece1; font-weight: 500; color: #6b655c; }
  .hint { margin-top: 10px; font-size: 12px; color: #a8a094; }
  .err { color: #b4492f; }
  .dash h3 { color: #1f4b45; margin: 0 0 16px; }
  .nums { display: flex; gap: 16px; flex-wrap: wrap; }
  .num { background: #fffdf9; border: 1px solid #e5dfd3; border-radius: 10px; padding: 16px 24px; min-width: 120px; }
  .num .v { display: block; font-size: 28px; font-weight: 600; color: #1f4b45; }
  .num .l { display: block; margin-top: 4px; font-size: 12px; color: #8a8378; }
  .dash p { color: #6b655c; font-size: 13px; }
</style>
