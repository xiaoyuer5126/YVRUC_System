/** 界面语言：中文、英文两种，只翻志愿者端。
 *
 *  自己写而不引第三方库：这里只要「查表 + 插值 + 切换后重绘」三件事，引一个
 *  库要连它的锁文件、占位符语法、复数规则一起接进来，不合算。
 *
 *  语言记在 localStorage，下次打开还是上次选的那种；默认中文。
 */
import { computed, ref } from 'vue'
import zh from './zh'
import en from './en'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import enUs from 'element-plus/es/locale/lang/en'

const KEY = 'yvruc_locale'
export const LOCALES = ['zh', 'en']
export const LOCALE_LABELS = { zh: '中文', en: 'EN' }

export const locale = ref(LOCALES.includes(localStorage.getItem(KEY))
  ? localStorage.getItem(KEY) : 'zh')

const TABLES = { zh, en }

/** Element Plus 自己的语言包：日期选择器、分页、表格空态这些组件内的文字 */
export const elLocale = computed(() => (locale.value === 'en' ? enUs : zhCn))

export function setLocale(next) {
  if (!LOCALES.includes(next)) return
  locale.value = next
  localStorage.setItem(KEY, next)
  document.documentElement.lang = next === 'en' ? 'en' : 'zh-CN'
}

document.documentElement.lang = locale.value === 'en' ? 'en' : 'zh-CN'

/** 取一条文案。第二个参数是插值用的对象：t('square.remaining', { n: 3 })。
 *
 *  英文表里缺某个键时退回中文，页面不会出现空白；查不到就返回键本身，
 *  一眼能看出漏翻了哪一条。
 */
export function t(key, params) {
  const table = TABLES[locale.value] || zh
  let text = table[key]
  if (text === undefined) text = zh[key]
  if (text === undefined) return key
  if (!params) return text
  return text.replace(/\{(\w+)\}/g, (m, name) =>
    (params[name] === undefined ? m : String(params[name])))
}

/** 按当前语言挑后端给的一对值，如 { name_zh, name_en }。没有英文就用中文。 */
export const pick = pair => (!pair ? '' : (locale.value === 'en' && pair.en ? pair.en : pair.zh))

export default { t, locale, elLocale, setLocale }
