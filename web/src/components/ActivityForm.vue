<template>
  <el-form label-width="110px">
    <el-form-item label="活动名称" required>
      <el-input v-model="f.title" maxlength="128" placeholder="如：校运动会开幕式志愿服务" />
    </el-form-item>
    <el-form-item label="组织单位" required>
      <el-input v-model="f.organizer" maxlength="64"
                placeholder="如：中国人民大学青年志愿者协会 / 图书馆" />
    </el-form-item>
    <el-form-item label="活动类型" required>
      <el-radio-group v-model="f.recruit_mode">
        <el-radio-button v-for="(m, key) in modes" :key="key" :value="key">
          {{ m.name }}
        </el-radio-button>
      </el-radio-group>
      <div class="dim" style="line-height:1.8">
        {{ modes[f.recruit_mode]?.desc }}
        <template v-if="f.recruit_mode === 'screened'">
          ；报名问卷在「岗位设置」中设计，由管理者统一分配
        </template>
      </div>
    </el-form-item>
    <el-form-item label="活动地点" required>
      <el-input v-model="f.location" placeholder="如：中关村校区世纪馆北门" />
    </el-form-item>
    <el-form-item label="联系人" required>
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
    <!-- 招募范围：两个都留空 = 面向全校。更细的差别走场次/岗位的条件要求 -->
    <el-form-item label="招募范围">
      <div class="two-col">
        <el-select v-model="f.recruit_campuses" multiple clearable collapse-tags
                   placeholder="校区（不限）">
          <el-option v-for="c in campuses" :key="c" :value="c" :label="c" />
        </el-select>
        <el-select v-model="f.recruit_stages" multiple clearable collapse-tags
                   placeholder="学段（不限）">
          <el-option v-for="s in stages" :key="s" :value="s" :label="s" />
        </el-select>
      </div>
      <div class="dim">留空即不限</div>
    </el-form-item>
    <el-form-item label="报名时间" required>
      <div class="two-col">
        <DateTimeField v-model="regStart" placeholder="报名开始时间"
                       :default-time="DAY_START" />
        <DateTimeField v-model="regEnd" placeholder="报名截止时间"
                       :default-time="DAY_END" />
      </div>
      <div class="dim">活动时间不用填，会根据场次时间自动计算。</div>
    </el-form-item>
    <el-form-item label="活动简介" required>
      <el-input v-model="f.intro" type="textarea" :rows="3"
                placeholder="介绍活动背景、服务对象等" />
    </el-form-item>
    <!-- 活动级的整体要求与保障；岗位上的「要求原文」讲的是那个岗位特有的事 -->
    <el-form-item label="志愿者要求">
      <el-input v-model="f.volunteer_requirement" type="textarea" :rows="2"
                placeholder="选填。整个活动共通的要求，如：着深色衣裤、提前 20 分钟集合" />
    </el-form-item>
    <el-form-item label="志愿者保障">
      <el-input v-model="f.volunteer_benefit" type="textarea" :rows="2"
                placeholder="选填。提供什么，如：午餐与饮水、志愿时长、纪念证书、意外险" />
    </el-form-item>
    <el-form-item label="志愿者联络群二维码" required>
      <div class="contacts-box">
        <el-upload :show-file-list="false" :http-request="uploadQr"
                   accept=".png,.jpg,.jpeg,.webp,image/png,image/jpeg,image/webp">
          <el-button type="primary" plain :loading="qrUploading">
            <el-icon><Upload /></el-icon>&nbsp;上传二维码图片
          </el-button>
        </el-upload>
        <div class="dim">
          志愿者联络群，png/jpg/webp 不超过 2MB。志愿者报名成功后加入联络群
        </div>
        <div v-if="f.contact_group_qr" class="qr-preview">
          <img :src="f.contact_group_qr" alt="志愿者联络群二维码" />
          <el-button link type="danger" size="small" @click="f.contact_group_qr = ''">移除图片</el-button>
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
import { computed, onMounted, ref } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Upload } from '@element-plus/icons-vue'
import { api } from '../api'
import { DAY_END, DAY_START } from '../store'
import { uploadImage } from '../composables/uploadImage'
import DateTimeField from './DateTimeField.vue'

const props = defineProps({
  modelValue: { type: Object, required: true },   // 表单数据
  range: { type: Array, default: () => [] },      // 报名时间 [开始, 结束]
  modes: { type: Object, default: () => ({}) },
})
const emit = defineEmits(['update:modelValue', 'update:range'])

const f = computed(() => props.modelValue)
// 起止各自成一个字段，拼回调用方拿的 [开始, 截止]
function setRange(i, v) {
  const next = [props.range?.[0] || '', props.range?.[1] || '']
  next[i] = v || ''
  emit('update:range', next[0] || next[1] ? next : [])
}
const regStart = computed({ get: () => props.range?.[0] || '',
                            set: v => setRange(0, v) })
const regEnd = computed({ get: () => props.range?.[1] || '',
                          set: v => setRange(1, v) })
const qrUploading = ref(false)
const campuses = ref([])
const stages = ref([])

onMounted(async () => {
  const c = await api.get('/api/auth/constants', { silent: true })
  campuses.value = c.campuses || []
  stages.value = c.stages || []
})

async function uploadQr({ file }) {
  qrUploading.value = true
  try {
    f.value.contact_group_qr = await uploadImage(file)
    ElMessage.success('二维码已上传')
  } catch (e) {
    ElMessage.error(e.message || '上传失败')
  } finally { qrUploading.value = false }
}
</script>

<style scoped>
.contacts-box { width: 100%; }
.contact-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
.qr-preview { margin-top: 8px; display: flex; align-items: center; gap: 12px; }
.qr-preview img { width: 96px; height: 96px; object-fit: contain;
                  border: 1px solid #eceef1; border-radius: 8px; }
.dim { margin-top: 4px; }
/* 两个选择框并排占满一行；窄屏自动叠成两行 */
.two-col { display: flex; gap: 8px; width: 100%; flex-wrap: wrap; }
.two-col > * { flex: 1 1 200px; min-width: 0; }
</style>
