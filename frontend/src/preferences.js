import { ref, watch } from 'vue'
import messages from './messages.json'

function saved(key) {
  try { return localStorage.getItem(key) } catch { return null }
}
function persist(key, value) {
  try { localStorage.setItem(key, value) } catch { /* Preferences still work without storage. */ }
}

export const thinkingExpanded = ref(saved('thinking-expanded') !== 'false')
watch(thinkingExpanded, value => persist('thinking-expanded', String(value)))

const storedLocale = saved('locale')
export const locale = ref(['zh', 'en'].includes(storedLocale) ? storedLocale : navigator.language.toLowerCase().startsWith('zh') ? 'zh' : 'en')
export const theme = ref(['light', 'dark'].includes(saved('theme')) ? saved('theme') : matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light')

export function t(key, values = {}) {
  const message = messages[key]?.[locale.value] || key
  return message.replace(/\{(\w+)\}/g, (match, name) => values[name] ?? match)
}
export function toggleTheme() { theme.value = theme.value === 'dark' ? 'light' : 'dark' }
export function toggleLocale() { locale.value = locale.value === 'zh' ? 'en' : 'zh' }

watch(theme, value => {
  document.documentElement.dataset.theme = value
  document.documentElement.style.colorScheme = value
  persist('theme', value)
}, { immediate: true })
watch(locale, value => {
  document.documentElement.lang = value === 'zh' ? 'zh-CN' : 'en'
  document.title = t('document.title')
  persist('locale', value)
}, { immediate: true })
