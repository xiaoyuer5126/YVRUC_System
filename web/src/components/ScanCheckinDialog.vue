<!-- 现场扫码签到/签退。

     为什么要在平台里扫，而不是让学生用系统相机扫完再跳回来：跳到浏览器再
     跳回来这一步，人容易在微信和浏览器之间转丢；而且签到成功与否他当场看不
     到。这个框扫到码就直接调接口，结果就地显示。

     扫到的码不显示、也不留痕——它是别人现场出示的凭据，落地页那边同样只出
     二维码不出码文。 -->
<template>
  <el-dialog :model-value="modelValue" title="扫码签到" :width="narrow ? '92vw' : '340px'"
             :close-on-click-modal="false" @close="close">
    <div class="scan-box">
      <!-- muted + playsinline：iOS 上不这样写，video 根本不出画面 -->
      <video ref="videoEl" muted playsinline></video>
      <div v-if="hint" class="scan-hint" :class="{ bad: bad }">{{ hint }}</div>
    </div>
    <p class="dim" style="text-align:center;margin:10px 0 0">
      把现场二维码放进框里，扫到自动完成
    </p>
  </el-dialog>
</template>

<script setup>
import { nextTick, onUnmounted, ref, watch } from 'vue'
import { BrowserQRCodeReader } from '@zxing/browser'
import { ElMessage } from 'element-plus'
import { api } from '../api'
import { useNarrow } from '../composables/useNarrow'

const props = defineProps({ modelValue: { type: Boolean, default: false } })
const emit = defineEmits(['update:modelValue', 'done'])

const narrow = useNarrow()
const videoEl = ref(null)
const hint = ref('')
const bad = ref(false)          // 报错态：换个颜色，别和「正在打开摄像头」混在一起

const reader = new BrowserQRCodeReader()
let controls = null
let busy = false                // 成功之后就锁住，别在关弹窗那一两秒里再扫一次
let last = { code: '', at: 0 }  // 同一张码别连着打接口（摄像头一秒能读十几帧）

/** 扫到的那串东西 → 签到码。二维码里编的是落地页网址，取最后一段。 */
function codeOf(text) {
  const raw = String(text || '').trim()
  const m = raw.match(/\/checkin\/([A-Za-z0-9]{6,16})(?:[/?#].*)?$/)
  const code = (m ? m[1] : raw).toUpperCase()
  return /^[A-Z0-9]{6,16}$/.test(code) ? code : ''
}

function fail(msg) {
  hint.value = msg
  bad.value = true
}

function onResult(result) {
  if (!result || busy) return
  const code = codeOf(result.getText())
  if (!code) return                                  // 扫到别的二维码：当没看见
  const t = Date.now()
  if (code === last.code && t - last.at < 5000) return
  last = { code, at: t }
  submit(code)
}

async function submit(code) {
  busy = true
  hint.value = '正在签到…'
  bad.value = false
  try {
    // skipErrorToast：失败的话得留在框里（见下），再弹一个同样内容的飘字是重复
    const d = await api.post(`/api/checkin/task/${code}`, undefined,
                             { skipErrorToast: true })
    ElMessage.success(d.message)
    emit('done', d)
    emit('update:modelValue', false)
  } catch (e) {
    // 码过期、不是本场次、刚签到过——重新扫同一个码也一样，先把话说清楚；
    // 提示留在框里而不是弹个飘字：飘字三秒就没了，人举着手机对着同一张码
    // 反复扫，却不知道为什么一直不成功。现场那张码正在换，所以不停扫描，
    // 让人直接扫下一张。
    fail(e.message || '签到失败')
    busy = false
  }
}

async function start() {
  bad.value = false
  hint.value = '正在打开摄像头…'
  // 上一次是签完到关掉的（那时 busy 是 true，锁着不再收扫到的码）。不在这里
  // 解开，签退就永远扫不动：摄像头开着、画面在动、扫多少次都没反应。
  busy = false
  last = { code: '', at: 0 }
  await nextTick()
  try {
    controls = await reader.decodeFromConstraints(
      { video: { facingMode: { ideal: 'environment' } } }, videoEl.value, onResult)
    hint.value = ''
  } catch (e) {
    // 打不开的原因就那么几种：没授权、没有摄像头、或者不是 https
    fail(!window.isSecureContext
      ? '摄像头只能在 https 下使用，请用平台正式网址打开'
      : `打不开摄像头：${e?.message || e}`)
  }
}

function stop() {
  try { controls?.stop() } catch { /* 已经停了 */ }
  controls = null
}

function close() {
  stop()
  busy = false
  last = { code: '', at: 0 }
  hint.value = ''
  bad.value = false
  emit('update:modelValue', false)
}

// 关掉就得停：不停的话摄像头灯一直亮着，手机上还会一直发热
watch(() => props.modelValue, v => { if (v) start(); else stop() })
onUnmounted(stop)
</script>

<style scoped>
.scan-box {
  position: relative; width: 100%; aspect-ratio: 1 / 1; overflow: hidden;
  border-radius: 10px; background: #000;
}
.scan-box video { width: 100%; height: 100%; object-fit: cover; display: block; }
.scan-hint {
  position: absolute; left: 0; right: 0; bottom: 0; padding: 8px 10px;
  font-size: 13px; color: #fff; background: rgba(0, 0, 0, .55);
  text-align: center; line-height: 1.5;
}
.scan-hint.bad { background: rgba(190, 30, 30, .85); }
</style>
