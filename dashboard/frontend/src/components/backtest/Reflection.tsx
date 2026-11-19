// AI 解读: optional, asked for with a button, written in the background (POST /runs/{id}/reflect, then
// pollReflection on GET /runs/{id}?include_result=false every 2 s, bounded). It is the model's reading of
// the numbers above, labelled as such; its text is shown as plain text, never as markup.
import { Sparkles } from 'lucide-react'
import { Badge, Button, InlineAlert } from '@/components/ui'
import { plainReading } from '@/features/backtest/result'
import type { ReflectionView } from './hooks'

export interface ReflectionProps {
  view: ReflectionView
  starting: boolean
  onStart: () => void
}

export function Reflection({ view, starting, onStart }: ReflectionProps) {
  const result = view.result
  const pending = view.status === 'pending'
  return (
    <section aria-label="AI 解读" className="flex min-w-0 flex-col gap-3">
      <div className="flex flex-wrap items-center justify-between gap-2">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="text-title text-fg">AI 解读</h3>
          <Badge tone="warn">模型的看法，不是事实</Badge>
          {result?.source === 'rules' && <Badge>按规则生成（没有用模型）</Badge>}
        </div>
        {view.status !== 'completed' && (
          <Button size="sm" icon={Sparkles} loading={starting || pending} onClick={onStart}>
            {view.status === 'failed' || view.status === 'timeout' ? '再生成一次' : '生成 AI 解读'}
          </Button>
        )}
      </div>
      <p className="text-table text-fg-3">只根据上面的结果数字写成，可能说错；判断以上面的数字和提醒为准。不会自动生成。</p>
      {pending && <p role="status" className="text-body text-fg-2">解读生成中，可以先看上面的结果…</p>}
      {(view.status === 'failed' || view.status === 'timeout') && <InlineAlert tone="warn">{view.message ?? '解读没有生成'}</InlineAlert>}
      {view.status === 'completed' && result && (
        <div className="flex min-w-0 flex-col gap-3 border-l-2 border-line pl-3">
          {result.analysis && <p className="whitespace-pre-wrap break-words text-body text-fg-2">{plainReading(result.analysis)}</p>}
          {(result.lessons ?? []).length > 0 && (
            <div>
              <h4 className="text-table font-medium text-fg-2">要点</h4>
              <ul className="mt-1 list-disc pl-5 text-body text-fg-2">
                {(result.lessons ?? []).map((item, index) => <li key={index} className="break-words">{plainReading(item)}</li>)}
              </ul>
            </div>
          )}
          {(result.improvements ?? []).length > 0 && (
            <div>
              <h4 className="text-table font-medium text-fg-2">可以再检验的地方</h4>
              <ul className="mt-1 list-disc pl-5 text-body text-fg-2">
                {(result.improvements ?? []).map((item, index) => <li key={index} className="break-words">{plainReading(item)}</li>)}
              </ul>
            </div>
          )}
        </div>
      )}
    </section>
  )
}
