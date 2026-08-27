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
      <el-form-item label="身份证号" required>
        <el-input v-model="form.id_card" placeholder="18 位居民身份证号" maxlength="18" />
      </el-form-item>
    </el-form>
    <p class="dim" style="margin:0;font-size:12px;line-height:1.7">
      信息仅用于志愿服务组织联络与保险登记；身份证号仅存储、不对外展示。</p>
    <template #footer>
      <el-button v-if="!forced" @click="visible = false">取消</el-button>
      <el-button type="primary" :loading="saving" @click="save">保存</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { auth } from '../store'

const emit = defineEmits(['saved'])

const visible = ref(false)
const forced = ref(false)
const saving = ref(false)
const form = ref({ phone: '', wechat: '', id_card: '' })

// 打开对话框。forced=true 时不可关闭（资料没补全就不能报名）
function open(isForced = false) {
  form.value = {
    phone: auth.user?.phone || '',
    wechat: auth.user?.wechat || '',
    id_card: auth.user?.id_card || '',
  }
  forced.value = isForced
  visible.value = true
}
defineExpose({ open })

async function save() {
  const phone = form.value.phone.trim()
  const wechat = form.value.wechat.trim()
  const idCard = form.value.id_card.trim().toUpperCase()
  if (!phone || !wechat || !idCard) {
    return ElMessage.warning('手机号、微信号、身份证号均为必填项')
  }
  if (!/^1[3-9]\d{9}$/.test(phone)) {
    return ElMessage.warning('手机号格式不正确')
  }
  // 末位校验码由后端严格校验，这里先挡住明显填错的
  if (!/^\d{17}[\dX]$/.test(idCard)) {
    return ElMessage.warning('身份证号应为 18 位（末位可为 X）')
  }
  saving.value = true
  try {
    const d = await api.put('/api/me/profile', { phone, wechat, id_card: idCard })
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
