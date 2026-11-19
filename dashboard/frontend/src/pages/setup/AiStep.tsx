// 5 AI 助手（可选）: the chat model's provider, model and key, saved through the existing settings file, and
// 测试连接 with the backend's fixed wording. The key field is a password field that starts empty, is
// never filled from the server (which never sends the key) and is cleared after saving. The request goes
// straight from the field to POST /api/setup/llm; nothing caches it.
import { useState, type FormEvent } from 'react'
import { useQuery } from '@tanstack/react-query'
import { PlugZap, Save } from 'lucide-react'
import {
  Badge, Button, Checkbox, Field, InlineAlert, Input, Panel, SegmentedControl, Select, Skeleton, toast,
} from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { SETUP_LLM_KEY, fetchLlmSettings, saveLlm, testLlm } from '@/services/setup'
import {
  LLM_FEATURES, canTest, formErrors, initialForm, keyStatus, knownModel, presetOf, saveBody, switchPreset, testText, testTone,
  visionOf, type LlmForm,
} from '@/features/setup/llm'
import type { LlmSettings, LlmTestResult } from '@/features/setup/types'
import { useAppMode } from '@/components/shell/useAppMode'

const CUSTOM = '__custom__'

function ModelForm({ settings, refresh }: { settings: LlmSettings; refresh: () => Promise<unknown> }) {
  const { readOnlyReason } = useAppMode()
  const [form, setForm] = useState<LlmForm>(() => initialForm(settings))
  const [errors, setErrors] = useState<Record<string, string>>({})
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const [testing, setTesting] = useState(false)
  const [result, setResult] = useState<LlmTestResult | null>(null)
  const preset = presetOf(settings, form.preset)
  const listed = knownModel(preset, form.model)
  const [typing, setTyping] = useState(() => !listed)
  const current = settings.current
  const patch = (next: Partial<LlmForm>) => {
    setForm((value) => ({ ...value, ...next }))
    setResult(null)
  }

  const save = async (event: FormEvent) => {
    event.preventDefault()
    const found = formErrors(form, settings)
    setErrors(found)
    if (Object.keys(found).length || !preset) return
    setSaving(true)
    setFailure(null)
    try {
      await saveLlm(saveBody(form, preset))
      toast.success(`已保存：${preset.label} / ${form.model.trim()}`)
      await refresh()
    } catch (exc) {
      setFailure(getApiErrorMessage(exc))
    } finally {
      // Saved or not, the key does not stay in the page.
      setForm((value) => ({ ...value, apiKey: '' }))
      setSaving(false)
    }
  }

  const test = async () => {
    setTesting(true)
    setResult(null)
    try {
      setResult(await testLlm())
    } catch (exc) {
      setResult({ ok: false, code: 'request', message: `没有完成测试：${getApiErrorMessage(exc)}` })
    } finally {
      setTesting(false)
    }
  }

  const modelOptions = [
    ...(preset?.models ?? []).map((model) => ({ value: model.id, label: `${model.name}${model.supports_vision ? ' · 支持图片' : ''}` })),
    { value: CUSTOM, label: '其他模型（手动填写）' },
  ]
  const testable = canTest(current, form.preset, form)
  const status = keyStatus(current, form.preset, preset, form)
  return (
    <form onSubmit={(event) => void save(event)} noValidate className="flex flex-col gap-4">
      <Field label="服务商" error={errors.preset}>
        <SegmentedControl
          options={settings.presets.map((item) => ({ value: item.id, label: item.label }))}
          value={form.preset}
          onChange={(id) => {
            const next = presetOf(settings, id)
            if (!next) return
            setForm((value) => switchPreset(value, next))
            setTyping(next.models.length === 0)
            setResult(null)
            setErrors({})
          }}
        />
      </Field>
      {preset && <p className="-mt-2 text-table text-fg-3">{preset.description}</p>}
      <div className="grid gap-4 sm:grid-cols-2">
        {preset && preset.models.length > 0 && (
          <Field label="模型">
            <Select
              value={typing ? CUSTOM : form.model}
              onChange={(event) => {
                if (event.target.value === CUSTOM) {
                  setTyping(true)
                  patch({ model: '' })
                } else {
                  setTyping(false)
                  patch({ model: event.target.value })
                }
              }}
              options={modelOptions}
            />
          </Field>
        )}
        {(typing || !preset?.models.length) && (
          <Field label="模型名称" required hint="服务商文档里的模型 ID，如 llama3、qwen2.5" error={errors.model}>
            <Input value={form.model} autoComplete="off" spellCheck={false} onChange={(event) => patch({ model: event.target.value })} />
          </Field>
        )}
      </div>
      {preset?.base_url_editable ? (
        <Field label="API 地址" required hint="OpenAI 兼容接口的地址，通常以 /v1 结尾" error={errors.baseUrl}>
          <Input value={form.baseUrl} autoComplete="off" spellCheck={false} onChange={(event) => patch({ baseUrl: event.target.value })} />
        </Field>
      ) : preset && (
        <p className="text-table text-fg-3">API 地址：{preset.base_url}</p>
      )}
      <Field label="API 密钥" hint={status} error={errors.apiKey}>
        <Input
          type="password"
          value={form.apiKey}
          autoComplete="off"
          spellCheck={false}
          placeholder={status.startsWith('已保存') ? '已保存（不显示）' : '粘贴 API 密钥'}
          onChange={(event) => patch({ apiKey: event.target.value })}
        />
      </Field>
      {typing && (
        <Checkbox
          checked={form.supportsVision}
          onChange={(event) => patch({ supportsVision: event.target.checked })}
          label="这个模型能读图片"
          description="截图识别需要能读图片的模型；不确定时不要勾选。"
        />
      )}
      {!typing && preset && (
        <p className="text-table text-fg-2">
          {visionOf(preset, form) ? '这个模型能读图片，可用于截图识别。' : '这个模型不读图片：截图识别不可用，其他功能不受影响。'}
        </p>
      )}
      {failure && <InlineAlert tone="warn" title="没有保存">{failure}</InlineAlert>}
      <div className="flex flex-wrap items-center gap-2">
        <Button type="submit" variant="primary" icon={Save} loading={saving} disabledReason={readOnlyReason}>保存</Button>
        <Button
          icon={PlugZap}
          loading={testing}
          disabledReason={readOnlyReason ?? (testable ? undefined : '先保存这里的设置；测试连接只连已保存的地址')}
          onClick={() => void test()}
        >
          测试连接
        </Button>
      </div>
      {result && (
        <InlineAlert tone={testTone(result)} title={result.ok ? '连接成功' : '连接失败'}>
          {testText(result)}
        </InlineAlert>
      )}
    </form>
  )
}

export function AiStep({ refresh }: { refresh: () => Promise<unknown> }) {
  const settings = useQuery({ queryKey: SETUP_LLM_KEY, queryFn: fetchLlmSettings })
  const current = settings.data?.current
  return (
    <Panel
      title="AI 助手（可选）"
      description={`不配置也能记账、看行情和设提醒；${LLM_FEATURES}需要它。`}
      actions={current?.key.configured || current?.local ? <Badge tone="accent">已配置</Badge> : <Badge>未配置</Badge>}
    >
      {settings.isPending && <Skeleton lines={4} />}
      {settings.isError && (
        <InlineAlert tone="warn" title="读不到模型设置" action={<Button size="sm" onClick={() => void settings.refetch()}>重试</Button>}>
          {getApiErrorMessage(settings.error)}
        </InlineAlert>
      )}
      {settings.data && (
        <div className="flex flex-col gap-4">
          {current && (
            <p className="text-table text-fg-2">
              现在使用：{current.group_name} / {current.model}
              {current.preset === null && '（在 config.json 中手动配置；在这里保存会切换到所选服务商）'}
            </p>
          )}
          <ModelForm settings={settings.data} refresh={refresh} />
        </div>
      )}
    </Panel>
  )
}
