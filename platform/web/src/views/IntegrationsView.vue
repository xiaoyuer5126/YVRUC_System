<template>
  <div class="page page-wide">
    <h2 style="margin-top:0">集成设置（外部系统对接）</h2>
    <el-alert type="info" :closable="false" style="margin-bottom:14px">
      平台与四个外部系统的对接均已完成<b>接口层</b>：关闭开关时使用内置演示实现（数据不出校园网），
      学校信息化部门提供正式参数后，打开开关并填入地址/密钥即可切换到真实接口，业务代码无需改动。
    </el-alert>

    <el-row :gutter="16" v-if="configs.length">
      <el-col :span="12" v-for="c in configs" :key="c.key" style="margin-bottom:16px">
        <el-card shadow="never" class="int-card">
          <template #header>
            <div style="display:flex;align-items:center;gap:10px">
              <b>{{ c.label }}</b>
              <el-tag v-if="!c.enabled" size="small" type="info" effect="plain">演示模式（Mock）</el-tag>
              <el-tag v-else size="small" type="success" effect="plain">已对接真实接口</el-tag>
            </div>
          </template>

          <p class="dim" style="margin-top:0">{{ c.mode_hint }}</p>
          <ul class="caps dim">
            <li v-for="cap in c.capabilities" :key="cap">{{ cap }}</li>
          </ul>

          <el-form label-width="120px" label-position="left" style="margin-top:8px"
                   v-if="editKey === c.key">
            <el-form-item v-for="f in fieldsOf(c)" :key="f.key" :label="f.label">
              <el-input v-model="draft[f.key]" :placeholder="f.holder || ''" />
            </el-form-item>
            <div style="text-align:right">
              <el-button size="small" @click="editKey = ''">取消</el-button>
              <el-button size="small" type="primary" @click="save(c)">保存配置</el-button>
            </div>
          </el-form>

          <template v-else>
            <div class="kv dim" v-for="f in fieldsOf(c)" :key="f.key">
              <span>{{ f.label }}：</span>
              <span class="mono">{{ mask(f.key, c[f.key]) }}</span>
            </div>
            <div style="margin-top:10px;display:flex;gap:8px">
              <el-button size="small" @click="startEdit(c)">编辑参数</el-button>
              <el-switch v-model="c.enabled" active-text="启用真实接口"
                         @change="toggleEnabled(c)" />
            </div>
          </template>
        </el-card>
      </el-col>
    </el-row>

    <h3>对接要点备忘（《设计方案》第五节）</h3>
    <el-table :data="notes" size="small" border>
      <el-table-column prop="sys" label="系统" width="150" />
      <el-table-column prop="use" label="用途" min-width="260" />
      <el-table-column prop="how" label="对接方式建议" min-width="300" />
    </el-table>
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'

const configs = ref([])
const editKey = ref('')
const draft = ref({})

const FIELD_LABELS = {
  base_url: '服务地址', app_key: 'AppKey', app_secret: 'AppSecret',
  corp_id: '企业ID', agent_id: '应用AgentId', corp_secret: '应用Secret',
  protocol: '协议', authorize_url: '授权端点', token_url: 'Token端点',
  user_info_url: '用户信息端点', client_id: 'ClientId', client_secret: 'ClientSecret',
  redirect_uri: '回调地址',
}
const SECRET_KEYS = new Set(['app_secret', 'corp_secret', 'client_secret'])
const PLACEHOLDERS = {
  shuzhi_renda_base_url: 'https://shuzhi.ruc.edu.cn/openapi',
  wecom_work_corp_id: 'ww 开头的企业ID',
}

function fieldsOf(c) {
  return Object.keys(FIELD_LABELS)
    .filter(k => k in c && !k.includes('url') || ['base_url', 'authorize_url', 'token_url', 'user_info_url', 'redirect_uri'].includes(k))
    .filter(k => k in c)
    .map(k => ({ key: k, label: FIELD_LABELS[k], holder: PLACEHOLDERS[`${c.key}_${k}`] }))
}
function mask(key, val) {
  if (!val) return '未配置'
  if (SECRET_KEYS.has(key)) return String(val).slice(0, 3) + '******'
  return val
}
function startEdit(c) {
  editKey.value = c.key
  const d = {}
  for (const f of fieldsOf(c)) d[f.key] = c[f.key] || ''
  draft.value = d
}
const REQUIRED_FIELD = {
  shuzhi_renda: 'base_url', wecom_work: 'corp_id',
  checkin_gateway: 'base_url', sso: 'authorize_url',
}

async function toggleEnabled(c) {
  const reqKey = REQUIRED_FIELD[c.key]
  if (c.enabled && reqKey && !String(c[reqKey] || '').trim()) {
    ElMessage.warning(`尚未填写「${FIELD_LABELS[reqKey]}」，将保持演示模式运行；填好参数后才会真正调用外部接口`)
  }
  await api.put(`/api/staff/integrations/${c.key}`, { enabled: c.enabled })
  ElMessage.success(`${c.label} 已${c.enabled ? '切换到真实接口' : '切回演示模式'}`)
}
async function save(c) {
  await api.put(`/api/staff/integrations/${c.key}`, draft.value)
  ElMessage.success('已保存，立即生效')
  editKey.value = ''
  load()
}

async function load() { configs.value = await api.get('/api/staff/integrations') }
onMounted(load)

const notes = [
  { sys: '数智人大', use: '获取姓名、学号、手机、身份证号；活动结束回传时长',
    how: '只读API实时查询，或每日同步宽表（离线同步），二选一' },
  { sys: '企业微信', use: '向特定学号的学生发送模板消息（报名成功/候补转正/岗位分配/变更警告/信用提醒）',
    how: '调用企业微信应用消息API，模板库已在平台内建好' },
  { sys: '现有签到码系统', use: '生成签到任务、核验签到码、回调签到状态',
    how: 'API 对接（由对方提供 SDK 或 RESTful 接口）；平台侧任务模型已就绪' },
  { sys: '微人大统一身份认证', use: '师生登录平台（SSO）',
    how: 'OAuth2.0 或 CAS 协议；登录页与路由守卫已按回调流程预留' },
]
</script>

<style scoped>
.int-card { border-radius: 12px; }
.caps { margin: 4px 0; padding-left: 18px; line-height: 1.9; }
.kv { display:flex; gap:6px; line-height:2; }
.kv span:first-child { width:110px; }
</style>
