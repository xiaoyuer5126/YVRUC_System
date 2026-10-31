/** 界面语言：中文、英文两种，只翻志愿者端。
 *
 *  中文原文就是键：t('消息通知') 在中文界面下原样返回，英文界面下去 en.js 里
 *  查这句话的译文。这样不用另起一套键名，改动在 diff 里也看得懂，英文表漏了
 *  哪句就照原样显示中文，页面不会空。
 *
 *  语言记在 localStorage，下次打开还是上次选的那种；默认中文。
 */
import { computed, ref } from 'vue'
import en from './en'
import { auth } from '../store'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import enUs from 'element-plus/es/locale/lang/en'

const KEY = 'yvruc_locale'
export const LOCALES = ['zh', 'en']

const stored = localStorage.getItem(KEY)
export const locale = ref(LOCALES.includes(stored) ? stored : 'zh')

/** Element Plus 自己的语言包：日期选择器、分页、表格空态这些组件内的文字 */
export const elLocale = computed(() => (isEn.value ? enUs : zhCn))

/** 只在志愿者端翻：当前身份是管理员、活动发起方、青协时一律按中文显示。
 *  两边共用的组件（数据卡片、刷新条）因此不用各写一套。 */
export const zhOnly = computed(() => ['admin', 'initiator', 'qingxie']
  .includes(auth.activeIdentity))

/** 现在的语言是不是英文。模板里要按语言换整段措辞时用它。 */
export const isEn = computed(() => locale.value === 'en' && !zhOnly.value)

function applyLang() {
  document.documentElement.lang = locale.value === 'en' ? 'en' : 'zh-CN'
}

export function setLocale(next) {
  if (!LOCALES.includes(next)) return
  locale.value = next
  localStorage.setItem(KEY, next)
  applyLang()
}

applyLang()

/** 取一句文案。
 *
 *  params 可以是对象 t('欢迎，{name}', { name })，也可以是数组
 *  t('场次共 {0} 段', [n])——句子里夹着表达式时用数组，省得为它起名。
 *  英文表里没有这句时返回中文原文，漏翻只会看到中文，不会看到键名。
 */
export function t(text, params) {
  let out = text
  if (isEn.value && en[text] !== undefined) out = en[text]
  if (params === undefined) return out
  return out.replace(/\{(\w+)\}/g, (m, name) => {
    const v = Array.isArray(params) ? params[Number(name)] : params[name]
    return v === undefined ? m : String(v)
  })
}

const WEEKDAY_EN = { 一: 'Mon', 二: 'Tue', 三: 'Wed', 四: 'Thu', 五: 'Fri', 六: 'Sat', 日: 'Sun' }

/** 后端拼好的时间串（models.span_text / periods_text / days_text 那几种）：
 *  「2026-09-20（周六）09:00-12:00」，几段之间用「、」隔开，整体起止写成
 *  「2026-10-16（周五）~ 2026-10-17（周六）」，没有时间是「待定」。
 *  英文界面下星期换成英文缩写、段与段之间换成分号，其余原样。 */
export function tTime(text) {
  if (!text || !isEn.value) return text || ''
  if (text === '待定') return t('待定')
  return text.replace(/（周([一二三四五六日])）/g, (m, d) => ` (${WEEKDAY_EN[d]}) `)
    .replace(/\s*、\s*/g, '; ').replace(/ {2,}/g, ' ').trim()
}

/** 后端拼好的条件说明：「校区：中关村校区；学段：本科生/硕士研究生」，以及
 *  不符合条件的原因「学段需为 本科生（您是 硕士研究生）」。标签名与校区、学段、
 *  性别这些固定取值按 en.js 翻，学院名这类查不到的照原样显示。 */
export function tTags(text) {
  if (!text || !isEn.value) return text || ''
  const vals = s => s.split('/').map(v => t(v.trim())).join(' / ')
  return text.split('；').map(part => {
    const unmet = part.match(/^(.+?)需为 (.+)（您是 (.*)）$/)
    if (unmet) return t('{0}需为 {1}（您是 {2}）', [t(unmet[1]), vals(unmet[2]), t(unmet[3])])
    const i = part.indexOf('：')
    return i < 0 ? t(part) : `${t(part.slice(0, i))}: ${vals(part.slice(i + 1))}`
  }).join('; ')
}

/** 报酬方式（HoursRecord.reward_label）：「志愿时长」「劳务」或
 *  「志愿时长 3h + 劳务 2h」。 */
export function tReward(text) {
  if (!text || !isEn.value) return text || ''
  return text.replace(/志愿时长|劳务/g, m => t(m))
}

/** 签到、签退成功时后端回的那句话（routers/checkin.py）：
 *  「签到成功，张三 已到岗（本场第 2 次签到）。（距签到点约 30 米）」，
 *  括号里的两段可有可无。英文界面下按这几段重新拼；对不上格式的整句去查表。 */
export function tCheckin(text) {
  if (!text || !isEn.value) return text || ''
  const m = text.match(/^(签到|签退)成功，(.+?) (?:已到岗|本次服务时长将按签到签退计算)(?:（本场第 (\d+) 次签到）)?。(?:（距签到点约 (\d+) 米）)?$/)
  if (!m) return t(text)
  let out = t(m[1] === '签到' ? '签到成功，{0} 已到岗。' : '签退成功，{0} 本次服务时长将按签到签退计算。', [m[2]])
  if (m[3]) out += ' ' + t('本场第 {0} 次签到', [m[3]])
  if (m[4]) out += ' ' + t('距签到点约 {0} 米', [m[4]])
  return out
}

/** 岗位在列表里显示的名字：占位岗位的名字是后端写的固定说明，要翻；
 *  真实岗位名是负责人填的，原样显示。 */
export const posName = p => (p?.is_placeholder ? t(p.display_name) : (p?.display_name || ''))

/** 按当前语言挑后端给的一对值，如 { zh, en }。没有英文就用中文。 */
export const pick = pair => (!pair ? '' : (isEn.value && pair.en ? pair.en : pair.zh))

export default { t, locale, elLocale, setLocale }
