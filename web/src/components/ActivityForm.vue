<template>
  <el-form label-width="110px">
    <el-form-item label="活动名称" required>
      <el-input v-model="f.title" maxlength="128" :placeholder="titleExample" />
    </el-form-item>
    <el-form-item label="组织单位" required>
      <el-input v-model="f.organizer" maxlength="64"
                placeholder="如：中国人民大学青年志愿者协会 / 图书馆" />
    </el-form-item>
    <!-- 联合举办：组织单位仍是必填的那一个，这里填另一个主办方。
         对外显示成「组织单位 × 联合主办方」，由后端拼，前端不自己拼 -->
    <el-form-item label="联合主办方">
      <el-autocomplete v-model="f.co_organizer" :fetch-suggestions="coOrganizerSuggest"
                       :trigger-on-focus="false" maxlength="64" style="width:100%"
                       placeholder="选填，与别的单位合办时填另一个主办方" />
      <div class="dim">填了在活动页显示成「组织单位 × 联合主办方」；单独主办就留空</div>
    </el-form-item>
    <el-form-item label="活动类型" required>
      <el-radio-group v-model="f.recruit_mode">
        <el-radio-button v-for="(m, key) in shownModes" :key="key" :value="key">
          {{ m.name }}
        </el-radio-button>
      </el-radio-group>
      <div class="dim">
        {{ modes[f.recruit_mode]?.desc }}
        <template v-if="f.recruit_mode === 'screened'">
          ；报名问卷在「岗位设置」中设计，由管理者统一分配
        </template>
        <template v-else-if="isRecord">
          。不在广场展示、不接受报名；照常提交校青协审批，通过后在「名单导入」里导入学号与时长
        </template>
      </div>
    </el-form-item>
    <el-form-item label="活动地点" required>
      <el-input v-model="f.location" placeholder="如：中关村校区世纪馆北门" />
      <!-- 地图上的点选填：定了的话，志愿者在活动详情里能一键跳到地图应用导航 -->
      <div v-if="!isRecord" class="point-line">
        <template v-if="f.location_point">
          <span class="dim">已定位 {{ f.location_point.lat.toFixed(5) }},
            {{ f.location_point.lng.toFixed(5) }}</span>
          <el-button size="small" link type="primary" @click="openPoint">修改</el-button>
          <el-button size="small" link @click="f.location_point = null">清除</el-button>
        </template>
        <template v-else>
          <el-button size="small" plain @click="openPoint">在地图上定位</el-button>
          <span class="dim">选填，定位后志愿者可以一键导航</span>
        </template>
      </div>
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
    <!-- 补记录活动没有场次：线下哪几段办的直接填 -->
    <el-form-item v-if="isRecord" label="活动时间" required>
      <PeriodEditor v-model="f.held_periods" />
      <div class="dim">线下实际开展的时间，会写进时长明细与导出备注。</div>
    </el-form-item>
    <!-- 招募范围：全都留空 = 面向全校。更细的差别走场次/岗位的条件要求 -->
    <el-form-item v-if="!isRecord" label="招募范围">
      <div class="two-col">
        <el-select v-model="f.recruit_campuses" multiple clearable collapse-tags
                   placeholder="校区（不限）">
          <el-option v-for="c in campuses" :key="c" :value="c" :label="c" />
        </el-select>
        <el-select v-model="f.recruit_stages" multiple clearable collapse-tags
                   placeholder="学段（不限）">
          <el-option v-for="s in stages" :key="s" :value="s" :label="s" />
        </el-select>
        <!-- 学院选项从已有用户档案汇总（统一身份认证原文），要逐字匹配 -->
        <el-select v-model="f.recruit_colleges" multiple clearable collapse-tags
                   filterable placeholder="学院（不限）">
          <el-option v-for="c in colleges" :key="c" :value="c" :label="c" />
        </el-select>
      </div>
      <div class="dim">留空即不限</div>
    </el-form-item>
    <el-form-item v-if="!isRecord" label="报名时间" required>
      <div class="two-col">
        <DateTimeField v-model="regStart" placeholder="报名开始时间"
                       :default-time="DAY_START" />
        <DateTimeField v-model="regEnd" placeholder="报名截止时间"
                       :default-time="DAY_END" />
      </div>
      <div class="dim">活动时间不用填：在「岗位设置」里填好各场次的时间，活动时间按场次自动汇总显示。</div>
    </el-form-item>
    <el-form-item label="活动简介" required>
      <el-input v-model="f.intro" type="textarea" :rows="3"
                placeholder="介绍活动背景、服务对象等" />
    </el-form-item>
    <!-- 活动级的整体要求与保障；岗位上的「要求原文」讲的是那个岗位特有的事 -->
    <el-form-item v-if="!isRecord" label="志愿者要求">
      <el-input v-model="f.volunteer_requirement" type="textarea" :rows="2"
                placeholder="选填。整个活动共通的要求，如：着深色衣裤、提前 20 分钟集合" />
    </el-form-item>
    <el-form-item v-if="!isRecord" label="注意事项">
      <el-input v-model="f.precautions" type="textarea" :rows="2"
                placeholder="选填。提醒类的话，如：有体力劳动请穿深色衣服、自备口罩；花粉过敏者慎报" />
    </el-form-item>
    <el-form-item v-if="!isRecord" label="志愿者保障" required>
      <el-input v-model="f.volunteer_benefit" type="textarea" :rows="2"
                placeholder="提供什么，如：志愿时长、午餐与饮水、纪念证书、意外险" />
    </el-form-item>
    <el-form-item v-if="!isRecord" label="志愿者联络群二维码" required>
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
          <img v-if="qrSrc" :src="qrSrc" alt="志愿者联络群二维码" />
          <el-button link type="danger" size="small" @click="f.contact_group_qr = ''">移除图片</el-button>
        </div>
      </div>
    </el-form-item>
    <el-dialog v-model="pointDlg.visible" title="活动地点定位" width="440" append-to-body>
      <GeoPicker v-model="pointDlg.value" @place="name => pointDlg.place = name" />
      <template #footer>
        <el-button @click="pointDlg.visible = false">取消</el-button>
        <el-button type="primary" :disabled="!pointDlg.value.lat"
                   @click="savePoint">用这个位置</el-button>
      </template>
    </el-dialog>
  </el-form>
</template>

<script setup>
/**
 * 活动基本信息表单。新建和「编辑基本信息」共用这一份——两份各写一遍的时候，
 * 上一轮加的「志愿者要求／保障」只进了编辑那份，创建时压根没有。
 *
 * 只管字段与校验，不管按钮：调用方拿 validate() 校验、拿 body() 取提交体。
 */
import { computed, onMounted, ref, watch } from 'vue'
import { ElMessage } from 'element-plus'
import { Plus, Upload } from '@element-plus/icons-vue'
import { api } from '../api'
import { DAY_END, DAY_START } from '../store'
import { uploadImage } from '../composables/uploadImage'
import { useAuthedImage } from '../composables/authedImage'
import { RECORD_MODE } from '../composables/activityForm'
import DateTimeField from './DateTimeField.vue'
import PeriodEditor from './PeriodEditor.vue'
import GeoPicker from './GeoPicker.vue'

// 示例名称带上年月，提醒管理者把时间写进活动名称
const today = new Date()
const titleExample = `如：${today.getFullYear()}年${today.getMonth() + 1}月校运动会开幕式志愿服务`

const props = defineProps({
  modelValue: { type: Object, required: true },   // 表单数据
  range: { type: Array, default: () => [] },      // 报名时间 [开始, 结束]
  modes: { type: Object, default: () => ({}) },
  // 活动当前状态：补记录与招募活动之间只在草稿里切换（后端同样拦）
  status: { type: String, default: 'draft' },
})
const emit = defineEmits(['update:modelValue', 'update:range'])

const f = computed(() => props.modelValue)
// 二维码要鉴权，地址不能直接放进 <img>，得带凭据取回来（见 authedImage）
const qrSrc = useAuthedImage(() => f.value.contact_group_qr)
const isRecord = computed(() => f.value.recruit_mode === RECORD_MODE)
// 草稿之后：补记录活动只剩它自己，招募活动里不再出现补记录
const shownModes = computed(() => {
  if (props.status === 'draft') return props.modes
  return Object.fromEntries(Object.entries(props.modes)
    .filter(([key]) => (key === RECORD_MODE) === isRecord.value))
})
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
const colleges = ref([])
const coOrganizers = ref([])     // vuecheck: script-only，广场列表给的历史值，做建议用
const benefitDefault = ref('')   // vuecheck: script-only，只用来预填

// 新建时预填默认的保障（以后端为准，不在前端抄一份）；编辑时已有内容就不动。
// 新建弹窗关了再开，组件还在、表单换成一份新的空表：所以跟着表单对象填，
// 只在挂载时填一次的话，第二次打开就是空的
function fillBenefit() {
  if (!f.value.volunteer_benefit && benefitDefault.value) {
    f.value.volunteer_benefit = benefitDefault.value
  }
}
watch(() => props.modelValue, fillBenefit)

onMounted(async () => {
  const [c, cl, square] = await Promise.all([
    api.get('/api/auth/constants', { silent: true }),
    api.get('/api/staff/colleges', { silent: true }),
    // 只要候选项，所以 size=1；拉不到就没有建议，别的默认值照旧
    api.get('/api/activities?size=1', { silent: true }).catch(() => ({})),
  ])
  campuses.value = c.campuses || []
  stages.value = c.stages || []
  benefitDefault.value = c.volunteer_benefit_default || ''
  fillBenefit()
  colleges.value = cl.colleges || []
  coOrganizers.value = square.co_organizers || []
})

/** 联合主办方的输入建议：在已经用过的历史值里筛（后端按非空去重排好序）。 */
function coOrganizerSuggest(q, cb) {
  const key = (q || '').trim()
  cb(coOrganizers.value.filter(v => !key || v.includes(key)).map(v => ({ value: v })))
}

// —— 地点定位 ——
const pointDlg = ref({ visible: false, value: {}, place: '' })
function openPoint() {
  const p = f.value.location_point
  pointDlg.value = { visible: true, place: '',
                     value: { lat: p?.lat || 0, lng: p?.lng || 0, accuracy: 0, source: '' } }
}
function savePoint() {
  const { lat, lng } = pointDlg.value.value
  f.value.location_point = { lat, lng }
  // 地点还没写字的，拿选中的地名顺手填上；写了就不动，那是管理者自己的说法
  if (!f.value.location?.trim() && pointDlg.value.place) f.value.location = pointDlg.value.place
  pointDlg.value.visible = false
}

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
.point-line { display: flex; align-items: center; gap: 8px; flex-wrap: wrap;
              width: 100%; margin-top: 8px; }
.contact-row { display: flex; gap: 8px; margin-bottom: 8px; align-items: center; }
.qr-preview { margin-top: 8px; display: flex; align-items: center; gap: 12px; }
.qr-preview img { width: 96px; height: 96px; object-fit: contain;
                  border: 1px solid #eceef1; border-radius: 8px; }
.dim { margin-top: 4px; }
/* 两个选择框并排占满一行；窄屏自动叠成两行 */
.two-col { display: flex; gap: 8px; width: 100%; flex-wrap: wrap; }
.two-col > * { flex: 1 1 200px; min-width: 0; }
</style>
