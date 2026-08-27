<!-- 完善/修改个人信息。顶栏菜单和报名被拦截时都用它，校验只有这一份。 -->
<template>
  <el-dialog v-model="visible"
             :title="forced ? '完善个人信息' : '修改个人信息'"
             width="460px"
             :close-on-click-modal="false"
             :close-on-press-escape="!forced"
             :show-close="!forced">
    <el-alert v-if="forced" type="warning" :closable="false" style="margin-bottom:14px"
              title="数智人大无法同步您的联系方式，请先补全以下信息，否则不能报名志愿活动" />
    <el-form label-width="82px">
      <el-form-item label="手机号" required>
        <el-input v-model="form.phone" placeholder="11 位手机号" maxlength="11" />
      </el-form-item>
      <el-form-item label="微信号" required>
        <el-input v-model="form.wechat" placeholder="请输入微信号" maxlength="64" />
      </el-form-item>

      <el-form-item v-if="!form.no_mainland_id" label="身份证号"
                    :required="!hasIdOnFile">
        <!-- 已填过的只显示脱敏值，想改再点「修改」——否则看起来像没填 -->
        <div v-if="hasIdOnFile && !editingId" class="filled">
          <span class="mono">{{ maskedId }}</span>
          <el-button link type="primary" size="small" @click="editingId = true">
            修改
          </el-button>
        </div>
        <div v-else style="width:100%">
          <el-input v-model="form.id_card" maxlength="18"
                    :placeholder="hasIdOnFile ? '输入新的 18 位身份证号' : '18 位居民身份证号'" />
          <el-button v-if="hasIdOnFile" link size="small"
                     @click="editingId = false; form.id_card = ''">取消修改</el-button>
        </div>
      </el-form-item>

      <el-form-item label="">
        <el-checkbox v-model="form.no_mainland_id">
          我没有内地居民身份证号（港澳台学生／留学生）
        </el-checkbox>
      </el-form-item>
    </el-form>
    <p class="dim" style="margin:0;font-size:12px;line-height:1.7">
      信息仅用于志愿服务组织联络与保险登记；身份证号仅存储、不对外展示，
      导出的排班表也不包含。</p>
    <template #footer>
      <el-button v-if="!forced" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../store'

const emit = defineEmits(['saved'])

const visible = ref(false)
const forced = ref(false)
const saving = ref(false)
const editingId = ref(false)
const form = ref({ phone: '', wechat: '', id_card: '', no_mainland_id: false })

const maskedId = computed(() => auth.user?.id_card_masked || '')
const hasIdOnFile = computed(() => !!maskedId.value)

// 打开对话框。forced=true 时不可关闭（资料没补全就不能报名）
function open(isForced = false) {
  form.value = {
    phone: auth.user?.phone || '',
    wechat: auth.user?.wechat || '',
    id_card: '',                       // 永远不回填原号，只发改动
    no_mainland_id: !!auth.user?.no_mainland_id,
  }
  editingId.value = false
  forced.value = isForced
  visible.value = true
}
defineExpose({ open })

async function save() {
  const phone = form.value.phone.trim()
  const wechat = form.value.wechat.trim()
  const idCard = form.value.id_card.trim().toUpperCase()
  const noId = form.value.no_mainland_id

  if (!phone || !wechat) return ElMessage.warning('手机号与微信号均为必填项')
  if (!/^1[3-9]\d{9}$/.test(phone)) return ElMessage.warning('手机号格式不正确')
  if (!noId) {
    // 已填过且没点「修改」→ 留空提交表示这一项不动
    const mustFill = !hasIdOnFile.value || editingId.value
    if (mustFill && !/^\d{17}[\dX]$/.test(idCard)) {
      return ElMessage.warning('身份证号应为 18 位（末位可为 X）；'
        + '港澳台学生与留学生请勾选下方的免填选项')
    }
  }

  saving.value = true
  try {
    const d = await api.put('/api/me/profile', {
      phone, wechat, id_card: noId ? '' : idCard, no_mainland_id: noId,
    })
    auth.user = d.user
    localStorage.setItem('rucva_user', JSON.stringify(d.user))
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
.id-row { display: flex; align-items: center; gap: 10px; }
.mono { font-family: ui-monospace, monospace; letter-spacing: .5px; }
.filled { display: flex; align-items: center; gap: 10px; }
</style>
