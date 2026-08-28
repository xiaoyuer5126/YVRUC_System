<template>
  <el-form label-width="110px">
    <el-form-item label="活动名称" required>
      <el-input v-model="f.title" maxlength="128" placeholder="如：校运动会开幕式志愿服务" />
    </el-form-item>
    <el-form-item label="组织单位" required>
      <el-input v-model="f.organizer" maxlength="64"
                placeholder="如：校青协 / 文学院青年志愿者协会 / 校医院" />
      <div class="dim">谁办的这场活动，会展示给志愿者，也是审批时判断归口的依据</div>
    </el-form-item>
    <el-form-item label="活动类型" required>
      <el-radio-group v-model="f.type">
        <el-radio-button v-for="(t, key) in types" :key="key" :value="key">
          {{ key }}类·{{ t.name }}
        </el-radio-button>
      </el-radio-group>
      <div class="dim" style="line-height:1.8">
        <b>{{ f.type }}类·{{ types[f.type]?.full }}</b>：{{ types[f.type]?.desc }}<br />
        类型决定违规后的限制准入口径（《志愿者服务规范》）。
      </div>
    </el-form-item>
    <el-form-item label="活动地点" required>
      <el-input v-model="f.location" placeholder="如：世纪馆北门" />
    </el-form-item>
    <el-form-item label="公示联系方式" required>
      <div class="contacts-box">
        <div v-for="(c, i) in f.contacts" :key="i" class="contact-row">
          <el-input v-model="c.name" maxlength="32" placeholder="姓名" style="width:150px" />
          <el-input v-model="c.phone" maxlength="64" placeholder="电话/微信" style="flex:1" />
          <el-button type="danger" plain :disabled="f.contacts.length <= 1"
                     @click="f.contacts.splice(i, 1)">删除</el-button>
        </div>
        <el-button plain size="small" @click="f.contacts.push({ name: '', phone: '' })">
          <el-icon><Plus /></el-icon>&nbsp;添加联系人
        </el-button>
      </div>
    </el-form-item>
    <el-form-item label="报名窗口" required>
      <DateRange v-model="range" start-placeholder="报名开始时间" end-placeholder="报名截止时间" />
      <div class="dim" style="line-height:1.7">
        活动时间不用填：会根据场次时间自动计算并显示。<br />
        截止时间不能晚于最后一个场次的开始时间；每个场次还会在<b>自己开场时</b>
        各自停止收人。
      </div>
    </el-form-item>
    <el-form-item label="活动简介" required>
      <el-input v-model="f.intro" type="textarea" :rows="3"
                placeholder="介绍活动内容、岗位与注意事项" />
    </el-form-item>
    <!-- 活动级的整体要求与保障；岗位上的「要求原文」讲的是那个岗位特有的事 -->
    <el-form-item label="志愿者要求">
      <el-input v-model="f.volunteer_requirement" type="textarea" :rows="2"
                placeholder="选填。整个活动共通的要求，如：着深色衣裤、提前 20 分钟到集合点" />
    </el-form-item>
    <el-form-item label="志愿者保障">
      <el-input v-model="f.volunteer_benefit" type="textarea" :rows="2"
                placeholder="选填。提供什么，如：午餐与饮水、志愿时长、纪念徽章、意外险" />
    </el-form-item>
    <el-form-item label="答疑群二维码">
      <div class="contacts-box">
        <el-upload :show-file-list="false" :http-request="uploadQr"
                   accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp">
          <el-button type="primary" plain :loading="qrUploading">
            <el-icon><Upload /></el-icon>&nbsp;上传二维码图片
          </el-button>
        </el-upload>
        <div class="dim">选填，png/jpg/webp 不超过 2MB；所有人可扫码入群</div>
        <div v-if="f.qa_group_qr" class="qr-preview">
          <img :src="f.qa_group_qr" alt="答疑群二维码" />
          <el-button link type="danger" size="small" @click="f.qa_group_qr = ''">移除图片</el-button>
        </div>
      </div>
    </el-form-item>
  </el-form>
</template>

<script setup>
/**
 * 活动基本信息表单。新建和「编辑基本信息」共用这一份——两份各写一遍的时候，
 * 上一轮加的「志愿者要求／保障」只进了编辑那份，创建时压根没有。
 *
 * 只管字段与校验，不管按钮：调用方拿 validate() 校验、拿 body() 取提交体。
 */
import { computed, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Upload } from '@element-plus/icons-vue'
import { auth } from '../store'
import DateRange from './DateRange.vue'

const props = defineProps({
  modelValue: { type: Object, required: true },   // 表单数据
  range: { type: Array, default: () => [] },      // 报名窗口 [开始, 结束]
  types: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue', 'update:range'])

const f = computed(() => props.modelValue)
const range = computed({
  get: () => props.range,
  set: v => emit('update:range', v),
})
const qrUploading = ref(false)

async function uploadQr({ file }) {
  const fd = new FormData()
  fd.append('file', file)
  qrUploading.value = true
  try {
    const headers = {}
    if (auth.token) headers.Authorization = `Bearer ${auth.token}`
    const resp = await fetch('/api/staff/upload-image', { method: 'POST', headers, body: fd })
    const d = await resp.json().catch(() => ({}))
    if (!resp.ok) {
      throw new Error(typeof d.detail === 'string' ? d.detail : `上传失败(${resp.status})`)
    }
    f.value.qa_group_qr = d.url
    ElMessage.success('二维码已上传')
  } catch (e) {
    ElMessage.error(e.message || '上传失败')
  } finally { qrUploading.value = false }
}
</script>

<style scoped>
.contacts-box { width: 100%; }
.contact-row { display: flex; gap: 8px; margin-bottom: 6px; align-items: center; }
.qr-preview { margin-top: 8px; display: flex; align-items: center; gap: 10px; }
.qr-preview img { width: 96px; height: 96px; object-fit: contain;
                  border: 1px solid #eceef1; border-radius: 8px; }
.dim { margin-top: 4px; }
</style>
