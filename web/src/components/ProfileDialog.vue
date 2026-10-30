<!-- 完善/修改个人信息。顶栏菜单和报名被拦截时都用它，校验只有这一份。 -->
<template>
  <el-dialog v-model="visible"
             :title="forced ? '完善个人信息' : '修改个人信息'"
             width="460px"
             :close-on-click-modal="false"
             :close-on-press-escape="!forced"
             :show-close="!forced">
    <el-alert v-if="forced" type="warning" :closable="false"
              :title="t('以下信息学校不提供，请先补全后才能报名')" />
    <el-form label-width="82px">
      <el-form-item :label="t('手机号')" required>
        <el-input v-model="form.phone" :placeholder="t('11 位手机号')" maxlength="11" />
      </el-form-item>
      <el-form-item :label="t('微信号')" required>
        <el-input v-model="form.wechat" :placeholder="t('请输入微信号')" maxlength="64" />
        <!-- 只提示不校验：初始号也是能加上的，拦下来反而挡住一部分人 -->
        <div class="dim">{{ t('请注意，wxid_ 开头的初始微信号无法被搜索添加') }}</div>
      </el-form-item>
      <el-form-item :label="t('校区')" required>
        <!-- 学校接口里没有校区，只能本人选。它是招募范围的筛选项：不选的人
             会在「限校区」的活动上被静默挡掉，所以这里必填 -->
        <el-select v-model="form.campus" :placeholder="t('请选择所在校区')" style="width:100%">
          <el-option v-for="c in campuses" :key="c" :value="c" :label="c" />
        </el-select>
      </el-form-item>

      <el-form-item v-if="!form.no_mainland_id" :label="t('身份证号')"
                    :required="!hasIdOnFile">
        <!-- 已填过的只显示脱敏值，想改再点「修改」——否则看起来像没填 -->
        <div v-if="hasIdOnFile && !editingId" class="filled">
          <span class="mono">{{ maskedId }}</span>
          <el-button link type="primary" size="small" @click="editingId = true">{{ t('修改') }}</el-button>
        </div>
        <div v-else style="width:100%">
          <el-input v-model="form.id_card" maxlength="18"
                    :placeholder="hasIdOnFile ? '输入新的 18 位身份证号' : '18 位身份证号'" />
          <el-button v-if="hasIdOnFile" link size="small"
                     @click="editingId = false; form.id_card = ''">{{ t('取消修改') }}</el-button>
        </div>
      </el-form-item>

      <el-form-item label="">
        <el-checkbox v-model="form.no_mainland_id">{{ t('我没有内地居民身份证号（港澳台学生／留学生）') }}</el-checkbox>
      </el-form-item>
    </el-form>
    <VbRegisterTip v-if="forced" />
    <p class="dim" style="margin:0">{{ t('信息仅用于志愿服务组织联络，身份证号仅用于时长录入，不对外展示') }}</p>
    <template #footer>
      <el-button v-if="!forced" @click="visible = false">{{ t('取消') }}</el-button>
      <el-button type="primary" :loading="saving" @click="save">{{ t('保存') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../store'
import VbRegisterTip from './VbRegisterTip.vue'
import { t } from '../i18n'

const emit = defineEmits(['saved'])

const visible = ref(false)
const forced = ref(false)
const saving = ref(false)
const editingId = ref(false)
const form = ref({ phone: '', wechat: '', campus: '', id_card: '', no_mainland_id: false })
const campuses = ref([])

const maskedId = computed(() => auth.user?.id_card_masked || '')
const hasIdOnFile = computed(() => !!maskedId.value)

// 打开对话框。forced=true 时不可关闭（资料没补全就不能报名）
async function open(isForced = false) {
  form.value = {
    phone: auth.user?.phone || '',
    wechat: auth.user?.wechat || '',
    campus: auth.user?.campus || '',
    id_card: '',                       // 永远不回填原号，只发改动
    no_mainland_id: !!auth.user?.no_mainland_id,
  }
  editingId.value = false
  forced.value = isForced
  visible.value = true
  if (!campuses.value.length) {
    // 选项以后端 /api/auth/constants 为准，别在这里抄第二份（同 store.js）。
    // 只取一次，之后开这个对话框不再等网络。
    const c = await api.get('/api/auth/constants', { silent: true })
    campuses.value = c.campuses || []
  }
}
defineExpose({ open })

async function save() {
  const phone = form.value.phone.trim()
  const wechat = form.value.wechat.trim()
  const campus = form.value.campus
  const idCard = form.value.id_card.trim().toUpperCase()
  const noId = form.value.no_mainland_id

  if (!phone || !wechat) return ElMessage.warning(t('手机号与微信号均为必填项'))
  if (!campus) return ElMessage.warning(t('请选择所在校区'))
  if (!/^1[3-9]\d{9}$/.test(phone)) return ElMessage.warning(t('手机号格式不正确'))
  if (!noId) {
    // 已填过且没点「修改」→ 留空提交表示这一项不动
    const mustFill = !hasIdOnFile.value || editingId.value
    if (mustFill && !/^\d{17}[\dX]$/.test(idCard)) {
      return ElMessage.warning(t('身份证号应为 18 位（末位可为 X）'))
    }
  }

  saving.value = true
  try {
    const d = await api.put('/api/me/profile', {
      phone, wechat, campus, id_card: noId ? '' : idCard, no_mainland_id: noId,
    })
    auth.user = d.user
    localStorage.setItem('yvruc_user', JSON.stringify(d.user))
    const wasForced = forced.value
    visible.value = false
    ElMessage.success(wasForced ? '个人资料已完善，现在可以报名志愿活动了'
                                : '个人信息已更新')
    emit('saved', d.user)
  } finally {
    saving.value = false
  }
}
</script>

<style scoped>
.id-row { display: flex; align-items: center; gap: 12px; }
.mono { font-family: ui-monospace, monospace; letter-spacing: .5px; }
.filled { display: flex; align-items: center; gap: 12px; }
</style>
