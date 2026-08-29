/** 活动基本信息表单的共享逻辑：空表单、校验、提交体。
 *
 * 新建与「编辑基本信息」共用，配合 components/ActivityForm.vue。
 * 分开放是因为 <script setup> 里不能导出具名函数。
 */

export function emptyActivity() {
  return {
    title: '', organizer: '', type: 'A', location: '',
    contacts: [{ name: '', phone: '' }], intro: '',
    volunteer_requirement: '', volunteer_benefit: '', qa_group_qr: '',
    recruit_campuses: [], recruit_stages: [],
  }
}

/** 从活动详情回填表单（编辑用）。 */
export function activityToForm(act) {
  const contacts = Array.isArray(act.contacts) && act.contacts.length
    ? act.contacts.map(c => ({ name: c.name || '', phone: c.phone || '' }))
    : [{ name: '', phone: '' }]
  return {
    title: act.title, organizer: act.organizer || '', type: act.type,
    location: act.location, contacts, intro: act.intro,
    volunteer_requirement: act.volunteer_requirement || '',
    volunteer_benefit: act.volunteer_benefit || '',
    qa_group_qr: act.qa_group_qr || '',
    recruit_campuses: act.recruit_campuses || [],
    recruit_stages: act.recruit_stages || [],
  }
}

/** 返回第一条错误提示；无错返回 ''。报名窗口按 range 一起校验。 */
export function validateActivity(f, range) {
  if (!f.title?.trim()) return '请填写活动名称'
  if (!f.organizer?.trim()) return '请填写组织单位'
  if (!f.type) return '请选择活动类型'
  if (!f.location?.trim()) return '请填写活动地点'
  if (!f.contacts?.length) return '请至少添加一位公示联系人'
  for (const c of f.contacts) {
    if (!c.name?.trim()) return '请填写每位联系人的姓名'
    if (!c.phone?.trim()) return '请填写每位联系人的电话/微信'
  }
  if (!(range?.length === 2 && range[0] && range[1])) {
    return '请选择报名开始与截止时间'
  }
  if (!f.intro?.trim()) return '请填写活动简介'
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
  return {
    ...f,
    contacts: f.contacts.map(c => ({ name: c.name.trim(), phone: c.phone.trim() })),
    reg_start: toISO(range[0]),
    reg_end: toISO(range[1]),
  }
}
