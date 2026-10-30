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

/** 按当前语言挑后端给的一对值，如 { zh, en }。没有英文就用中文。 */
export const pick = pair => (!pair ? '' : (isEn.value && pair.en ? pair.en : pair.zh))

export default { t, locale, elLocale, setLocale }
