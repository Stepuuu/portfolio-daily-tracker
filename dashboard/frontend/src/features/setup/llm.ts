// The model step without React: the form's starting values, what is wrong with it, the body that is saved
// and how the key's state reads. The key is write-only: the form starts empty, is never prefilled from the
// server (which never sends it) and the page clears it after saving. Pure TypeScript (node tests).
import type { LlmCurrent, LlmPreset, LlmSaveBody, LlmSettings, LlmTestResult } from './types.ts'

export const LLM_FEATURES = '对话助手、简报的模型点评和截图识别（需要支持图片的模型）'
/** A saved key belongs to the address it was saved for (the backend refuses to send it anywhere else). */
export const NEW_ADDRESS_KEY = '换了 API 地址：请填写新地址的 API 密钥'
const MODEL = /^[A-Za-z0-9_./:+-]{1,160}$/
const MAX_KEY = 400
const DEFAULT_PORT: Record<string, string> = { 'http:': '80', 'https:': '443' }

export interface LlmForm {
  preset: string
  model: string
  baseUrl: string
  apiKey: string
  /** Only asked for models the preset does not know. */
  supportsVision: boolean
}

export function presetOf(settings: LlmSettings | undefined, id: string): LlmPreset | undefined {
  return settings?.presets.find((preset) => preset.id === id)
}

/** The saved choice when it is one of the presets, else the first preset and its first model. */
export function initialForm(settings: LlmSettings): LlmForm {
  const current = settings.current
  const saved = current?.preset ? presetOf(settings, current.preset) : undefined
  const preset = saved ?? settings.presets[0]
  if (!preset) return { preset: '', model: '', baseUrl: '', apiKey: '', supportsVision: false }
  const model = saved && current ? current.model : preset.models[0]?.id ?? ''
  return {
    preset: preset.id,
    model,
    baseUrl: saved && current?.base_url ? current.base_url : preset.base_url,
    apiKey: '',
    supportsVision: Boolean(saved && current?.supports_vision),
  }
}

/** A new preset starts from its own address and first model; the typed key is kept. */
export function switchPreset(form: LlmForm, preset: LlmPreset): LlmForm {
  return { ...form, preset: preset.id, model: preset.models[0]?.id ?? '', baseUrl: preset.base_url, supportsVision: false }
}

/** Whether the model is one the preset lists (its image support is then known). */
export function knownModel(preset: LlmPreset | undefined, model: string): boolean {
  return Boolean(preset?.models.some((entry) => entry.id === model))
}

export function visionOf(preset: LlmPreset | undefined, form: LlmForm): boolean {
  const known = preset?.models.find((entry) => entry.id === form.model)
  return known ? known.supports_vision : form.supportsVision
}

/** The backend's rule (core/llm_setup.py `check_base_url`): http(s), a host, no user, password, query or fragment. */
export function baseUrlProblem(text: string): string | null {
  const value = text.trim()
  const message = 'API 地址必须是 http:// 或 https:// 开头的地址，不能包含用户名、密码、查询或片段'
  if (!value || /\s/.test(value)) return message
  let url: URL
  try {
    url = new URL(value)
  } catch {
    return message
  }
  if (!['http:', 'https:'].includes(url.protocol) || !url.hostname || url.username || url.password || url.search
      || url.hash || value.includes('?') || value.includes('#')) return message
  return null
}

/** Scheme, host and port of an address ("https://api.example.test:443"); null when it is not one. */
function origin(text: string | null | undefined): string | null {
  try {
    const url = new URL((text ?? '').trim())
    return `${url.protocol}//${url.hostname}:${url.port || DEFAULT_PORT[url.protocol] || ''}`
  } catch {
    return null
  }
}

/** The address with its path, for comparing what is typed with what is saved. */
function target(text: string | null | undefined): string {
  const base = origin(text)
  if (!base) return (text ?? '').trim()
  return base + new URL((text ?? '').trim()).pathname.replace(/\/+$/, '')
}

function hostname(text: string): string | null {
  try {
    return new URL(text.trim()).hostname
  } catch {
    return null
  }
}

/** The backend's loopback rule (core/llm_setup.py `_loopback`): localhost, *.localhost, 127.0.0.0/8, ::1. */
function loopback(text: string): boolean {
  const host = hostname(text)
  return host !== null && (host === 'localhost' || host.endsWith('.localhost') || host === '[::1]' || /^127\.\d+\.\d+\.\d+$/.test(host))
}

/** The address the save sends: the typed one where it can be chosen, else the preset's own. */
function saved(form: LlmForm, preset: LlmPreset): string {
  return preset.base_url_editable ? form.baseUrl : preset.base_url
}

/**
 * Whether the save takes this address without a typed key, by the API's own rule for the preset (`keyless`:
 * the stored key's origin, the hosts whose key the server environment provides, loopback). Undefined for an
 * older payload without it.
 */
function keyFree(form: LlmForm, preset: LlmPreset | undefined): boolean | undefined {
  const rules = preset?.keyless
  if (!preset || !rules) return undefined
  const address = saved(form, preset)
  const at = origin(address)
  return (at !== null && rules.origins.includes(at)) || rules.env.some((entry) => entry.host === hostname(address))
    || (rules.loopback && loopback(address))
}

/** The saved preset's key, typed for another address (scheme, host or port) than the one in the form. */
function keyForAnotherAddress(current: LlmCurrent | null | undefined, form: LlmForm, preset: LlmPreset | undefined): boolean {
  if (preset?.keyless) return keyFree(form, preset) === false && preset.keyless.saved_key
  return Boolean(current && preset?.base_url_editable && current.preset === form.preset && current.key.configured
    && origin(current.base_url) !== origin(form.baseUrl))
}

/**
 * Whether a key is already in place for this preset: exactly where the API's `keyless` rule says the save
 * needs none (from an older payload: from the environment, saved for this address, or a local server).
 */
export function keyInPlace(current: LlmCurrent | null | undefined, form: LlmForm, preset: LlmPreset | undefined): boolean {
  const free = keyFree(form, preset)
  if (free !== undefined) return free
  if (preset?.env_key?.available) return true
  if (current && current.preset === form.preset && current.key.configured && !keyForAnotherAddress(current, form, preset)) return true
  return Boolean(preset?.base_url_editable && loopback(form.baseUrl))
}

export function formErrors(form: LlmForm, settings: LlmSettings | undefined): Record<string, string> {
  const errors: Record<string, string> = {}
  const preset = presetOf(settings, form.preset)
  if (!preset) errors.preset = '请选择服务商'
  if (!MODEL.test(form.model.trim())) errors.model = '请填写模型名称（字母、数字和 _ . / : + -）'
  if (preset?.base_url_editable) {
    const problem = baseUrlProblem(form.baseUrl)
    if (problem) errors.baseUrl = problem
  }
  const key = form.apiKey.trim()
  if (key.length > MAX_KEY) errors.apiKey = `密钥最多 ${MAX_KEY} 个字符`
  else if (key && (/\s/.test(key) || !/^[\x21-\x7e]+$/.test(key))) errors.apiKey = '密钥里有空格或无法识别的字符，请检查是否复制完整'
  else if (!key && !keyInPlace(settings?.current, form, preset)) {
    errors.apiKey = keyForAnotherAddress(settings?.current, form, preset) ? NEW_ADDRESS_KEY : '请填写 API 密钥'
  }
  return errors
}

/** `POST /api/setup/llm`. The key is sent only when one was typed (empty keeps the saved one). */
export function saveBody(form: LlmForm, preset: LlmPreset): LlmSaveBody {
  const body: LlmSaveBody = { preset: preset.id, model: form.model.trim() }
  if (preset.base_url_editable) body.base_url = form.baseUrl.trim()
  if (form.apiKey.trim()) body.api_key = form.apiKey.trim()
  if (!knownModel(preset, body.model)) body.supports_vision = form.supportsVision
  return body
}

/** How the key field's hint reads (for the preset and the form, when given). Never shows any part of a key. */
export function keyStatus(current: LlmCurrent | null | undefined, presetId: string, preset?: LlmPreset, form?: LlmForm): string {
  const env = preset?.keyless && form ? preset.keyless.env.find((entry) => entry.host === hostname(saved(form, preset))) : undefined
  if (env) return `使用环境变量 ${env.name} 中的密钥；在这里填写的不会覆盖它`
  if (preset?.env_key?.available) return `使用环境变量 ${preset.env_key.name} 中的密钥；在这里填写的不会覆盖它`
  if (!current || current.preset !== presetId) return '还没有保存这个服务商的密钥'
  if (current.key.source === 'env' && current.key.env) return `使用环境变量 ${current.key.env} 中的密钥；在这里填写的不会覆盖它`
  if (form && keyForAnotherAddress(current, form, preset)) {
    return loopback(form.baseUrl) ? '本机服务可以不填；已保存的密钥不会发往新地址' : NEW_ADDRESS_KEY
  }
  if (current.key.configured) return '已保存（不显示）；留空则继续使用'
  if (current.local) return '本机服务可以不填'
  return '还没有保存密钥'
}

/**
 * Whether 测试连接 can run: something is saved for the chosen preset, and (with the form) at the address the
 * form shows: the test always reaches the saved address, never one only typed.
 */
export function canTest(current: LlmCurrent | null | undefined, presetId: string, form?: LlmForm): boolean {
  if (!current || current.preset !== presetId || !(current.key.configured || current.local)) return false
  return !form || target(form.baseUrl) === target(current.base_url)
}

export function testTone(result: LlmTestResult): 'info' | 'warn' {
  return result.ok ? 'info' : 'warn'
}

/** "连接成功：模型已回应。（DeepSeek Chat，0.8 秒）". */
export function testText(result: LlmTestResult): string {
  if (!result.ok) return result.message
  const seconds = typeof result.latency_ms === 'number' ? `${(result.latency_ms / 1000).toFixed(1)} 秒` : null
  const extra = [result.model, seconds].filter(Boolean).join('，')
  return extra ? `${result.message}（${extra}）` : result.message
}
