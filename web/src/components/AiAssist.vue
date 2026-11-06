<!-- 智能起草的入口：页面右侧偏上一个悬浮的圆按钮，点开右侧抽屉；调用方也可以用 show() 直接打开。
     抽屉里贴原文 → 起草 → 看草稿（调用方的 #preview 插槽画）→ 调用方的 #footer
     按钮决定怎么用。平台只把草稿拿给人看：填进表单、建场次都要管理者自己点。
     后端没配 AI 的 key（/api/auth/constants 的 ai_enabled）时整个不出现。 -->
<template>
  <template v-if="enabled">
    <el-tooltip :content="title" placement="left">
      <button type="button" class="ai-fab" :class="{ grabbing: isDragging }"
              :style="fabStyle" :aria-label="title"
              @pointerdown="onDown" @pointermove="onMove"
              @pointerup="onUp" @pointercancel="onUp" @click="onClick">
        <img :src="aiIcon" alt="" draggable="false" />
      </button>
    </el-tooltip>
    <el-drawer v-model="open" :title="title" :size="narrow ? '100%' : '560px'" append-to-body>
      <div class="dim">{{ hint }}</div>
      <el-input v-model="text" type="textarea" :autosize="{ minRows: 6, maxRows: 14 }"
                :placeholder="placeholder" :maxlength="30000" show-word-limit
                style="margin-top:8px" />
      <!-- 附件只在点「起草」时随请求发出去，后端转成文字或原样发图，不留存 -->
      <el-upload v-model:file-list="files" :auto-upload="false" multiple
                 :limit="AI_FILE_LIMIT" :accept="AI_FILE_ACCEPT" class="ai-files"
                 :on-exceed="() => ElMessage.warning(`最多传 ${AI_FILE_LIMIT} 个文件`)">
        <el-button size="small">上传文件</el-button>
        <template #tip><div class="dim">{{ AI_FILE_TIP }}</div></template>
      </el-upload>
      <el-alert type="warning" :closable="false" show-icon
                title="原文和附件会发给 AI，不要上传身份证号、志愿者名单等个人信息。AI 可能出错，请逐项核对。" />
      <div class="toolbar">
        <el-button type="primary" :loading="busy" @click="go">
          {{ result ? '重新起草' : '起草' }}
        </el-button>
        <span v-if="busy" class="dim">正在智能起草，一般要十几秒到一分钟</span>
      </div>
      <template v-if="result">
        <el-alert v-if="result.notes?.length" type="info" :closable="false"
                  title="智能起草提醒">
          <div v-for="(n, i) in result.notes" :key="i">· {{ n }}</div>
        </el-alert>
        <slot name="preview" :result="result" />
      </template>
      <template v-if="result" #footer>
        <slot name="footer" :result="result" :close="() => { open = false }" />
      </template>
    </el-drawer>
  </template>
</template>

<script setup>
import { computed, onBeforeUnmount, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { AI_FILE_ACCEPT, AI_FILE_LIMIT, AI_FILE_TIP, aiEnabled } from '../ai'
import { useNarrow } from '../composables/useNarrow'
import aiIcon from '../assets/ai-draft.png'

const props = defineProps({
  title: { type: String, required: true },
  hint: { type: String, default: '' },
  placeholder: { type: String, default: '' },
  run: { type: Function, required: true },     // async (原文, 附件 File[]) => { …草稿, notes }
})

const narrow = useNarrow()
const enabled = ref(false)
const open = ref(false)
const text = ref('')
const files = ref([])          // el-upload 的文件列表，真正的 File 在 raw 上
const busy = ref(false)
const result = ref(null)

// —— 悬浮按钮：可拖到不挡事的位置，松手吸附到最近的一侧 ——
// 固定在一个角上会压住底下的按钮（手机上尤其明显）。位置记在本机，
// 换页、刷新都还在；窗口缩放时按边界收回可视区
const SIZE = 48
const POS_KEY = 'yvruc_ai_fab'
const pos = ref(null)         // vuecheck: script-only，按钮的 left/top（px）；模板只通过 fabStyle 读
const isDragging = ref(false)
let drag = null               // 按下时的起点；松手清空
let dragEndAt = 0             // 刚拖完的那一下不算点击

const margin = () => (window.innerWidth <= 860 ? 16 : 24)
const clamp = (p) => {
  const m = margin()
  return { x: Math.min(Math.max(p.x, m), window.innerWidth - SIZE - m),
           y: Math.min(Math.max(p.y, m), window.innerHeight - SIZE - m) }
}
// 默认停在右侧偏上：放在右下角大家注意不到，还会压住页面底部的按钮
const corner = () => {
  const m = margin()
  return { x: window.innerWidth - SIZE - m, y: Math.round(window.innerHeight * 0.3) }
}
const stored = () => {
  try {
    const p = JSON.parse(localStorage.getItem(POS_KEY) || 'null')
    if (p && Number.isFinite(p.x) && Number.isFinite(p.y)) return p
  } catch { /* 存坏了就用默认角 */ }
  return null
}
const fabStyle = computed(() => {
  const p = pos.value || corner()
  return { left: `${p.x}px`, top: `${p.y}px` }
})

function onDown(e) {
  if (e.pointerType === 'mouse' && e.button !== 0) return
  const cur = pos.value || corner()
  drag = { id: e.pointerId, px: e.clientX, py: e.clientY, x: cur.x, y: cur.y, moved: false }
  e.currentTarget.setPointerCapture?.(e.pointerId)
}

function onMove(e) {
  if (!drag || e.pointerId !== drag.id) return
  const dx = e.clientX - drag.px, dy = e.clientY - drag.py
  if (!drag.moved && Math.hypot(dx, dy) < 4) return   // 手一抖不算拖
  if (!drag.moved) { drag.moved = true; isDragging.value = true }
  pos.value = clamp({ x: drag.x + dx, y: drag.y + dy })
}

function onUp(e) {
  if (!drag || e.pointerId !== drag.id) return
  const moved = drag.moved
  drag = null
  isDragging.value = false
  if (!moved) return
  // 松手吸附到左右最近的一侧，纵向保持
  const m = margin()
  pos.value = { x: pos.value.x + SIZE / 2 < window.innerWidth / 2
                   ? m : window.innerWidth - SIZE - m, y: pos.value.y }
  localStorage.setItem(POS_KEY, JSON.stringify(pos.value))
  dragEndAt = Date.now()
}

function onClick() {
  if (Date.now() - dragEndAt < 300) return   // 刚拖完的那一下不打开抽屉
  show()
}

function show() { open.value = true }

function onResize() { pos.value = clamp(pos.value || corner()) }

onMounted(async () => {
  pos.value = clamp(stored() || corner())
  window.addEventListener('resize', onResize)
  enabled.value = await aiEnabled()
})
onBeforeUnmount(() => window.removeEventListener('resize', onResize))

async function go() {
  busy.value = true
  try {
    result.value = await props.run(text.value, files.value.map(f => f.raw))
  } catch { /* 错误提示 api 层已经给了 */ } finally {
    busy.value = false
  }
}

/** 草稿用掉之后清空，下次打开从头来 */
function reset() {
  result.value = null
  text.value = ''
  files.value = []
}
defineExpose({ reset, show, enabled })
</script>

<style scoped>
.ai-fab {
  position: fixed; z-index: 1000;   /* left/top 由拖拽逻辑给 */
  width: 48px; height: 48px; border-radius: 50%; border: none; cursor: grab;
  touch-action: none; user-select: none;
  transition: left .18s ease, top .18s ease;   /* 松手吸附的那一下 */
  /* 人大红为主、左上角亮一档的渐变；图标原图是浅灰线稿，滤镜提成纯白才衬得住 */
  background: linear-gradient(135deg, #d05a70 0%, var(--ruc-red) 78%);
  box-shadow: 0 4px 12px rgba(174, 11, 42, .35);
  display: flex; align-items: center; justify-content: center;
}
.ai-fab.grabbing { transition: none; cursor: grabbing; }
/* 图标不参与指针事件：按住中心时按下的是按钮本身，不会触发浏览器
   「拖图片」的原生拖拽——那一下会把 pointer 序列打断（pointercancel），
   按钮就跟不住手了 */
.ai-fab img { width: 26px; height: 26px; display: block;
              pointer-events: none; -webkit-user-drag: none;
              filter: brightness(0) invert(1); }
.ai-fab:hover { background: linear-gradient(135deg, #dc6b80 0%, #c00e35 78%); }

/* 上传控件默认是 inline-block，紧贴上面的输入框且只多一条行内基线缝；改成块级并留出间距 */
.ai-files { display: block; margin-top: 8px; }
</style>
