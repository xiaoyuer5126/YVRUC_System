<!-- 志愿者联络群二维码。报名成功/候补转正/录取时弹一次，之后在「我的报名」里点开看。
     二维码要鉴权，地址不能直接放进 <img>，得带凭据取回来（见 composables/authedImage）。 -->
<template>
  <el-dialog v-model="visible" :title="t('志愿者联络群')" width="380" align-center>
    <div class="qr-box">
      <img v-if="src" :src="src" alt="志愿者联络群二维码" />
      <p class="title">{{ title }}</p>
      <p class="dim">{{ t('请扫码加入志愿者联络群，入群后把昵称改为') }}<b>{{ t('姓名+电话') }}</b>{{ t('， 方便负责人统计联系。') }}</p>
      <p class="dim">{{ t('二维码可在「我的报名」中随时查看。') }}</p>
    </div>
    <template #footer>
      <el-button type="primary" @click="visible = false">{{ t('知道了') }}</el-button>
    </template>
  </el-dialog>
</template>

<script setup>
import { ref } from 'vue'
import { useAuthedImage } from '../composables/authedImage'
import { t } from '../i18n'

const visible = ref(false)
const qr = ref('')
const src = useAuthedImage(() => qr.value)
const title = ref('')

function open({ qr: image, title: name }) {
  if (!image) return
  qr.value = image
  title.value = name || ''
  visible.value = true
}
defineExpose({ open })
</script>

<style scoped>
.qr-box { text-align: center; }
.qr-box img { width: 200px; height: 200px; object-fit: contain;
              border: 1px solid #eceef1; border-radius: 10px; }
.title { margin: 12px 0 4px; font-weight: 600; }
.qr-box .dim { margin: 4px 0; line-height: 1.5; }
</style>
