/** 活动基本信息表单的共享逻辑：空表单、校验、提交体。
 *
 * 新建与「编辑基本信息」共用，配合 components/ActivityForm.vue。
 * 分开放是因为 <script setup> 里不能导出具名函数。
 */

export function emptyActivity() {
  return {
    title: '', organizer: '', recruit_mode: 'instant', location: '',
    contacts: [{ name: '', phone: '' }], intro: '',
    volunteer_requirement: '', volunteer_benefit: '', contact_group_qr: '',
    recruit_campuses: [], recruit_stages: [], recruit_colleges: [],
    // 补记录活动（不公开招募）自己的活动时间：它没有场次可算
    held_start: '', held_end: '',
  }
}

/** 不公开招募（补记录）：线下办完、只补记时长的活动。 */
export const RECORD_MODE = 'record'

/** 从活动详情回填表单（编辑用）。 */
export function activityToForm(act) {
  const contacts = Array.isArray(act.contacts) && act.contacts.length
    ? act.contacts.map(c => ({ name: c.name || '', phone: c.phone || '' }))
    : [{ name: '', phone: '' }]
  return {
    title: act.title, organizer: act.organizer || '', recruit_mode: act.recruit_mode,
    location: act.location, contacts, intro: act.intro,
    volunteer_requirement: act.volunteer_requirement || '',
    volunteer_benefit: act.volunteer_benefit || '',
    contact_group_qr: act.contact_group_qr || '',
    recruit_campuses: act.recruit_campuses || [],
    recruit_stages: act.recruit_stages || [],
    recruit_colleges: act.recruit_colleges || [],
    held_start: act.held_start || '', held_end: act.held_end || '',   // iso 带 T，各端都认
  }
}

/** 返回第一条错误提示；无错返回 ''。报名时间按 range 一起校验。 */
export function validateActivity(f, range) {
  if (!f.title?.trim()) return '请填写活动名称'
  if (!f.organizer?.trim()) return '请填写组织单位'
  if (!f.recruit_mode) return '请选择活动类型'
  if (!f.location?.trim()) return '请填写活动地点'
  if (!f.contacts?.length) return '请至少添加一位联系人'
  for (const c of f.contacts) {
    if (!c.name?.trim()) return '请填写每位联系人的姓名'
    if (!c.phone?.trim()) return '请填写每位联系人的电话/微信'
  }
  // 补记录活动不公开、不报名：要活动时间，不要报名时间和联络群
  if (f.recruit_mode === RECORD_MODE) {
    if (!f.held_start || !f.held_end) return '请填写活动开始与结束时间'
    if (new Date(f.held_end) <= new Date(f.held_start)) return '活动结束时间要晚于开始时间'
    if (!f.intro?.trim()) return '请填写活动简介'
    return ''
  }
  if (!(range?.length === 2 && range[0] && range[1])) {
    return '请选择报名开始与截止时间'
  }
  if (!f.intro?.trim()) return '请填写活动简介'
  if (!f.volunteer_benefit?.trim()) return '请填写志愿者保障'
  if (!f.contact_group_qr) return '请上传志愿者联络群二维码'
  return ''
}

function toISO(v) {
  if (!v) return null
  const d = v instanceof Date ? v : new Date(v)
  const p = n => String(n).padStart(2, '0')
  return `${d.getFullYear()}-${p(d.getMonth() + 1)}-${p(d.getDate())}`
       + `T${p(d.getHours())}:${p(d.getMinutes())}`
}

export function activityBody(f, range) {
  const contacts = f.contacts.map(c => ({ name: c.name.trim(), phone: c.phone.trim() }))
  if (f.recruit_mode === RECORD_MODE) {
    // 招募相关的字段不发：后端对补记录活动本来也不收
    const { contact_group_qr: _qr, recruit_campuses: _c, recruit_stages: _s,
            recruit_colleges: _cl, ...rest } = f
    return { ...rest, contacts,
             held_start: toISO(f.held_start), held_end: toISO(f.held_end) }
  }
  const { held_start: _hs, held_end: _he, ...rest } = f
  return { ...rest, contacts, reg_start: toISO(range[0]), reg_end: toISO(range[1]) }
}
