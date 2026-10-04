<template>
  <div class="page page-wide">
    <h2>集成设置（外部系统对接）</h2>
    <el-alert type="info" :closable="false">
      两个外部系统均已完成<b>接口层</b>对接：关闭开关时使用内置演示实现，
      填入学校信息化部门提供的地址与密钥并打开开关，即可切换到真实接口。
    </el-alert>

    <el-row :gutter="16" v-if="configs.length">
      <el-col :span="12" v-for="c in configs" :key="c.key" style="margin-bottom:16px">
        <el-card shadow="never" class="int-card">
          <template #header>
            <div style="display:flex;align-items:center;gap:8px">
              <b>{{ c.label }}</b>
              <el-tag v-if="!c.enabled" size="small" type="info" effect="plain">演示模式（Mock）</el-tag>
              <el-tag v-else size="small" type="success" effect="plain">已对接真实接口</el-tag>
            </div>
          </template>

          <p class="dim">{{ c.mode_hint }}</p>
          <ul class="caps dim">
            <li v-for="cap in c.capabilities" :key="cap">{{ cap }}</li>
          </ul>

          <el-form label-width="120px" label-position="left" style="margin-top:8px"
                   v-if="editKey === c.key">
            <el-form-item v-for="f in fieldsOf(c)" :key="f.key" :label="f.label">
              <el-input v-model="draft[f.key]" :placeholder="f.holder || ''" />
            </el-form-item>
            <div class="btn-row" style="display:flex;justify-content:flex-end">
              <el-button @click="editKey = ''">取消</el-button>
              <el-button type="primary" @click="save(c)">保存配置</el-button>
            </div>
          </el-form>

          <template v-else>
            <div class="kv dim" v-for="f in fieldsOf(c)" :key="f.key">
              <span>{{ f.label }}：</span>
              <span class="mono">{{ mask(f.key, c[f.key]) }}</span>
            </div>
            <div style="margin-top:12px;display:flex;gap:8px">
              <el-button @click="startEdit(c)">编辑参数</el-button>
              <el-switch v-model="c.enabled" active-text="启用真实接口"
                         @change="toggleEnabled(c)" />
            </div>
          </template>
        </el-card>
      </el-col>
    </el-row>

    <h3>对接要点备忘</h3>
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
  appid: '应用标识 AppId', gateway: '开放网关', channels: '发送通道',
  authorize_url: '授权端点', token_url: 'Token端点',
  user_info_url: '用户信息端点', client_id: 'ClientId', client_secret: 'ClientSecret',
  redirect_uri: '回调地址',
}
// 只在页面上打码显示用；这两项的密钥都存在 .env 里，不下发到前端
const SECRET_KEYS = new Set(['app_secret', 'client_secret'])
const PLACEHOLDERS = {
  msgcenter_gateway: 'https://opengw.ruc.edu.cn',
  msgcenter_appid: '开放平台应用标识。密钥在 .env 的 YVRUC_OPENPLATFORM_SECRET，不在此填写',
  msgcenter_channels: '4（企业微信）。1=短信、2=邮件——本平台不使用短信',
  sso_redirect_uri: '必须与应用中心登记的地址一字不差，差一个字符就换不到令牌',
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
// 与后端 integrations/__init__.py 的 REQUIRED_FIELD 一一对应。
// appid / client_id 默认来自 .env，测试环境为空 → 即便开关打开也回落 Mock。
const REQUIRED_FIELD = {
  msgcenter: 'appid', sso: 'client_id',
}

async function toggleEnabled(c) {
  const reqKey = REQUIRED_FIELD[c.key]
  if (c.enabled && reqKey && !String(c[reqKey] || '').trim()) {
    ElMessage.warning(`尚未填写「${FIELD_LABELS[reqKey]}」，将保持演示模式运行`)
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
  { sys: '消息中心（企业微信）', use: '向特定学号的学生发送模板消息（报名成功/候补转正/岗位分配/变更警告/信用提醒）',
    how: '经学校开放平台的统一消息接口下发，收件人用学号指定；模板库已在平台内建好，只走企业微信通道（不发短信）' },
  { sys: '微人大统一身份认证', use: '师生登录平台（SSO），首次登录自动建档',
    how: 'CAS 的 OAuth2.0 三端点（authorize / accessToken / user·profiles）；登录页、回调页与路由守卫已接通' },
]
</script>

<style scoped>
.int-card { border-radius: 12px; }
.caps { margin: 4px 0; padding-left: 18px; line-height: 1.6; }
.kv { display:flex; gap:8px; line-height: 1.6; }
.kv span:first-child { width:110px; }
</style>
