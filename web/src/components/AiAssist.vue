<!-- 智能起草的入口：页面右下角一个悬浮的圆按钮，点开右侧抽屉。
     抽屉里贴原文 → 起草 → 看草稿（调用方的 #preview 插槽画）→ 调用方的 #footer
     按钮决定怎么用。平台只把草稿拿给人看：填进表单、建场次都要管理者自己点。
     后端没配 AI 的 key（/api/auth/constants 的 ai_enabled）时整个不出现。 -->
<template>
  <template v-if="enabled">
    <el-tooltip :content="title" placement="left">
      <button type="button" class="ai-fab" :aria-label="title" @click="open = true">
        <img :src="aiIcon" alt="" />
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
                title="原文和附件会发给 AI 服务，别传身份证号、志愿者名单这类个人信息。草稿可能有错，用之前请逐项核对。" />
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
import { onMounted, ref } from 'vue'
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

onMounted(async () => { enabled.value = await aiEnabled() })

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
defineExpose({ reset })
</script>

<style scoped>
.ai-fab {
  position: fixed; right: 24px; bottom: 24px; z-index: 1000;
  width: 48px; height: 48px; border-radius: 50%; border: none; cursor: pointer;
  /* 人大红为主、左上角亮一档的渐变；图标原图是浅灰线稿，滤镜提成纯白才衬得住 */
  background: linear-gradient(135deg, #d05a70 0%, var(--ruc-red) 78%);
  box-shadow: 0 4px 12px rgba(158, 34, 53, .35);
  display: flex; align-items: center; justify-content: center;
}
.ai-fab img { width: 26px; height: 26px; display: block;
              filter: brightness(0) invert(1); }
.ai-fab:hover { background: linear-gradient(135deg, #dc6b80 0%, #b02540 78%); }
@media (max-width: 860px) {
  .ai-fab { right: 16px; bottom: 16px; }
}
</style>
