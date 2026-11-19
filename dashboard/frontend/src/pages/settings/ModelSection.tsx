// 设置 › 模型: the chat assistant's API group and model (the configured groups of `/api/settings/config`, one
// 保存), what is saved for its provider (`/api/setup/llm`: the key is never sent back, only whether one is
// set) with a link to the setup wizard's AI step to change the provider or the key, and the research
// workbench's model connections (the same editor as on 研究 › 接入与安排). Writes take readOnlyReason.
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { KeyRound } from 'lucide-react'
import { Badge, Button, Field, InlineAlert, Panel, Select, Skeleton, buttonClass, cx, toast } from '@/components/ui'
import LabConnections from '@/components/lab/LabConnections'
import { useAppMode } from '@/components/shell/useAppMode'
import { NO_ASSISTANT_REASON } from '@/lib/navigation'
import { getApiErrorMessage } from '@/services/api'
import { CHAT_CONFIG_KEY, PartialSave, fetchChatConfig, saveChatModel, type ChatConfig } from '@/services/settings'
import { SETUP_LLM_KEY, fetchLlmSettings } from '@/services/setup'
import { LLM_FEATURES } from '@/features/setup/llm'
import type { LlmCurrent } from '@/features/setup/types'

function ChatModelForm({ config }: { config: ChatConfig }) {
  const client = useQueryClient()
  const { readOnlyReason: writeBlocked, lab } = useAppMode()
  // Research-only mode (`make lab`) starts no chat assistant, so its model cannot be switched there (503).
  const readOnlyReason = writeBlocked ?? (lab ? NO_ASSISTANT_REASON : undefined)
  const [group, setGroup] = useState(config.current_api_group)
  const [model, setModel] = useState(config.current_model)
  const [saving, setSaving] = useState(false)
  const [failure, setFailure] = useState<string | null>(null)
  const models = config.api_groups[group]?.models ?? []
  const chosen = models.find((item) => item.id === model)

  async function save(event: FormEvent) {
    event.preventDefault()
    if (readOnlyReason) return
    setSaving(true)
    setFailure(null)
    try {
      const changed = await saveChatModel(config, group, model)
      toast.success(changed.length ? `已保存对话模型：${config.api_groups[group]?.name ?? group} / ${chosen?.name ?? model}` : '没有需要保存的改动')
    } catch (error) {
      setFailure(error instanceof PartialSave ? `${error.message}：${getApiErrorMessage(error.reason)}` : getApiErrorMessage(error))
    } finally {
      setSaving(false)
      await client.invalidateQueries({ queryKey: CHAT_CONFIG_KEY })
      await client.invalidateQueries({ queryKey: SETUP_LLM_KEY })
    }
  }

  return (
    <form onSubmit={(event) => void save(event)} noValidate className="flex flex-col gap-4">
      <div className="grid gap-4 sm:grid-cols-2">
        <Field label="API 组">
          <Select
            value={group}
            disabled={saving}
            onChange={(event) => {
              setGroup(event.target.value)
              setModel(config.api_groups[event.target.value]?.models[0]?.id ?? '')
            }}
            options={Object.entries(config.api_groups).map(([key, value]) => ({ value: key, label: value.name }))}
          />
        </Field>
        <Field label="模型" hint={chosen?.description || config.api_groups[group]?.description || undefined}>
          <Select
            value={model}
            disabled={saving || !models.length}
            onChange={(event) => setModel(event.target.value)}
            options={models.map((item) => ({ value: item.id, label: `${item.name}${item.supports_vision ? ' · 支持图片' : ''}` }))}
          />
        </Field>
      </div>
      {failure && <InlineAlert tone="warn" title="没有全部保存">{failure}</InlineAlert>}
      {lab && !writeBlocked && <p className="text-table text-fg-3">{NO_ASSISTANT_REASON}。</p>}
      <div>
        <Button type="submit" variant="primary" loading={saving} disabled={!group || !model} disabledReason={readOnlyReason}>
          保存
        </Button>
      </div>
    </form>
  )
}

/** Whether a key is in place, never any part of it (the backend does not send it). */
function keyText(current: LlmCurrent): string {
  if (current.key.source === 'env' && current.key.env) return `使用环境变量 ${current.key.env} 中的密钥`
  if (current.key.configured) return '已保存（不显示）'
  if (current.local) return '本机服务，可以不填'
  return '还没有保存'
}

function KeyLine() {
  const llm = useQuery({ queryKey: SETUP_LLM_KEY, queryFn: fetchLlmSettings })
  const current = llm.data?.current
  return (
    <div className="flex flex-col gap-2 border-t border-line pt-4">
      <div className="flex flex-wrap items-center gap-2 text-table text-fg-2">
        <KeyRound size={14} aria-hidden className="text-fg-3" />
        {llm.isPending && <Skeleton w={240} />}
        {llm.isError && <span>服务商和密钥：不可用（{getApiErrorMessage(llm.error)}）</span>}
        {llm.data && (current ? (
          <>
            <span>服务商 {current.group_name} · 密钥{keyText(current)}</span>
            {current.key.configured || current.local ? <Badge tone="accent">已配置</Badge> : <Badge>未配置</Badge>}
          </>
        ) : <span>还没有配置模型。</span>)}
      </div>
      <p className="text-table text-fg-3">
        换服务商、填写或更换 API 密钥在设置向导的 AI 助手一步；密钥只写入后端的配置文件，不会再显示。
      </p>
      <Link to="/setup?step=ai" className={cx(buttonClass({ size: 'sm' }), 'w-fit')}>
        在设置向导中修改服务商或密钥
      </Link>
    </div>
  )
}

export function ModelSection() {
  const config = useQuery({ queryKey: CHAT_CONFIG_KEY, queryFn: fetchChatConfig })
  return (
    <>
      <Panel title="对话模型" description={`对话助手使用的模型；${LLM_FEATURES}也用它。研究工作台的每个实验另选模型连接。`}>
        <div className="flex flex-col gap-4">
          {config.isPending && <Skeleton lines={3} />}
          {config.isError && (
            <InlineAlert tone="warn" title="读不到模型设置" action={<Button size="sm" onClick={() => void config.refetch()}>重试</Button>}>
              {getApiErrorMessage(config.error)}
            </InlineAlert>
          )}
          {/* Keyed by what is saved: a save here or in the wizard starts the form from the new values. */}
          {config.data && <ChatModelForm key={`${config.data.current_api_group}:${config.data.current_model}`} config={config.data} />}
          <KeyLine />
        </div>
      </Panel>
      <LabConnections />
    </>
  )
}

export default ModelSection
