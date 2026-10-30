<template>
  <div class="page page-wide">
    <h2>{{ t('平台公告') }}</h2>
    <p class="dim" style="margin-top:-8px">{{ t('启用后，每个用户登录平台时弹出一次。修改公告不会再弹给已经登录的人，他们下次登录时看到。') }}</p>
    <el-card shadow="never" v-loading="loading">
      <el-form label-position="top">
        <el-form-item :label="t('标题')">
          <el-input v-model="form.title" maxlength="60" show-word-limit :placeholder="t('如：试运行期间欢迎反馈')" />
        </el-form-item>
        <el-form-item :label="t('正文')">
          <el-input v-model="form.body" type="textarea" :rows="8" maxlength="2000" show-word-limit
                    :placeholder="t('支持换行；以 http:// 或 https:// 开头的网址会显示成链接')" />
        </el-form-item>
        <el-form-item :label="t('启用')">
          <el-switch v-model="form.enabled" active-text="登录时弹出" />
        </el-form-item>
      </el-form>
      <div class="foot">
        <span class="dim" v-if="meta.updated_at">{{ t('{0} 最后修改于 {1}', [meta.updated_by_name || '管理员', fmtDT(meta.updated_at)]) }}</span>
        <div class="btn-row">
          <el-button :disabled="!form.title.trim() || !form.body.trim()" @click="preview = true">{{ t('预览') }}</el-button>
          <el-button type="primary" :loading="saving" @click="save">{{ t('保存') }}</el-button>
        </div>
      </div>
    </el-card>

    <AnnouncementDialog v-model="preview" :title="form.title" :body="form.body" />
  </div>
</template>

<script setup>
import { onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api, fmtDT } from '../api'
import AnnouncementDialog from '../components/AnnouncementDialog.vue'
import { t } from '../i18n'

const loading = ref(false)
const saving = ref(false)
const preview = ref(false)
const form = ref({ title: '', body: '', enabled: false })
const meta = ref({})

function fill(d) {
  form.value = { title: d.title, body: d.body, enabled: d.enabled }
  meta.value = d
}

async function load() {
  loading.value = true
  try {
    fill(await api.get('/api/staff/announcement'))
  } finally { loading.value = false }
}

async function save() {
  saving.value = true
  try {
    fill(await api.put('/api/staff/announcement', form.value))
    ElMessage.success(form.value.enabled ? '已保存，用户下次登录时弹出' : '已保存，公告未启用')
  } finally { saving.value = false }
}

onMounted(load)
</script>

<style scoped>
.foot { display: flex; align-items: center; justify-content: space-between; gap: 8px; flex-wrap: wrap; }
.foot .btn-row { margin-left: auto; }
</style>
