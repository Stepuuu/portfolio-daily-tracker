// 快速回测's form: 标的 → 方法 (one sentence each, its numbers editable) → 区间 → the assumptions in one
// line with 修改 → 开始回测. 高级设置 holds 本金, 佣金 (commission only; taxes are added by date), 滑点, 每手,
// 复权 and the index to compare with; empty fields keep the market's defaults from GET /defaults.
import { useId, useState, type ReactNode } from 'react'
import { ChevronDown, ChevronRight, Play } from 'lucide-react'
import {
  Button, Checkbox, DateInput, Field, InlineAlert, Input, MoneyInput, NumberInput, SegmentedControl, Select, cx, focusRing,
} from '@/components/ui'
import { TickerInput } from '@/components/inputs/TickerInput'
import { fmtMoney } from '@/lib/format'
import {
  PERIODS, changedSettings, feeDefaultsLine, fractionToPercentText, isDateParam, methodParams, paramHint, paramUnit, periodRange,
  type FieldErrors, type FormState, type Method, type PeriodPreset,
} from '@/features/backtest/model'
import type { Preset, StrategyInfo } from '@/features/backtest/types'
import { useDefaults } from './hooks'

export interface QuickFormProps {
  form: FormState
  onChange: (next: FormState) => void
  presets: Preset[]
  /** Why the method list is missing (GET /presets failed or has none). */
  presetsProblem: string | null
  strategies: StrategyInfo[]
  errors: FieldErrors
  /** Where a link came from (来自持仓 ...). */
  notes: string[]
  /** A plan's prices converted to %, by parameter key. */
  conversions: Record<string, string>
  /** Research-only mode: no holdings or watchlist search (desk endpoints), a plain code field. */
  lab: boolean
  today: string
  running: boolean
  onRun: () => void
}

function MethodCard({ name, summary, checked, onSelect, group }: {
  name: string; summary: string; checked: boolean; onSelect: () => void; group: string
}) {
  return (
    <label
      className={cx(
        'flex cursor-pointer items-start gap-3 rounded-md border px-3 py-2 transition-colors duration-[120ms]',
        'has-[:focus-visible]:outline has-[:focus-visible]:outline-2 has-[:focus-visible]:outline-offset-2 has-[:focus-visible]:outline-accent',
        checked ? 'border-accent ring-1 ring-inset ring-accent' : 'border-line hover:border-line-input',
      )}
    >
      <input type="radio" name={group} checked={checked} onChange={onSelect} className="mt-1 h-4 w-4 shrink-0 accent-[rgb(var(--accent))]" />
      <span className="min-w-0">
        <span className="block text-body font-medium text-fg">{name}</span>
        <span className="block text-table text-fg-2">{summary}</span>
      </span>
    </label>
  )
}

function Disclosure({ open, onToggle, children, controls }: { open: boolean; onToggle: () => void; children: ReactNode; controls: string }) {
  const Icon = open ? ChevronDown : ChevronRight
  return (
    <button
      type="button"
      aria-expanded={open}
      aria-controls={controls}
      onClick={onToggle}
      className={cx('inline-flex items-center gap-1 rounded-sm text-body font-medium text-fg-2 hover:text-fg', focusRing)}
    >
      <Icon size={16} aria-hidden />
      {children}
    </button>
  )
}

export function QuickForm({
  form, onChange, presets, presetsProblem, strategies, errors, notes, conversions, lab, today, running, onRun,
}: QuickFormProps) {
  const group = useId()
  const advancedId = useId()
  const [advanced, setAdvanced] = useState(false)
  const defaults = useDefaults(form.ticker)
  const set = (patch: Partial<FormState>) => onChange({ ...form, ...patch })
  const setParam = (key: string, value: string) => onChange({ ...form, params: { ...form.params, [key]: value } })
  const choose = (method: Method) => onChange({ ...form, method, params: {} })
  const params = methodParams(form.method, { presets, strategies })
  const strategy = form.method?.kind === 'strategy' ? strategies.find((item) => item.id === form.method!.id) : undefined
  const preset = form.method?.kind === 'preset' ? presets.find((item) => item.id === form.method!.id) : undefined
  const range = periodRange(form.period, today, form)
  const currency = defaults.data?.currency ?? null
  const changed = changedSettings(form, currency, defaults.data)
  const advancedErrors = ['capital', 'commissionBuy', 'commissionSell', 'minCommission', 'slippage', 'lotSize', 'adjust', 'benchmark']
    .some((key) => errors[key as keyof FieldErrors])
  const showAdvanced = advanced || advancedErrors

  const tickerHint = defaults.loading ? '正在识别…' : defaults.data ? defaults.data.line_zh : lab ? '代码，如 SHA:600519、600519、0700.HK、AAPL' : '代码或名称；点开列表可选持仓'
  const tickerError = errors.ticker ?? defaults.error ?? undefined

  return (
    <form
      noValidate
      aria-label="快速回测"
      className="flex min-w-0 flex-col gap-5"
      onSubmit={(event) => {
        event.preventDefault()
        onRun()
      }}
    >
      {notes.length > 0 && <p className="text-table text-fg-2">{notes.join(' · ')}</p>}

      <Field label="标的" required hint={tickerHint} error={tickerError}>
        {lab ? (
          <Input value={form.ticker} onChange={(event) => set({ ticker: event.target.value })} placeholder="如 SHA:600519" autoComplete="off" spellCheck={false} />
        ) : (
          <TickerInput value={form.ticker} onChange={(ticker) => set({ ticker })} showName />
        )}
      </Field>

      <fieldset className="flex min-w-0 flex-col gap-2">
        <legend className="mb-1 text-table font-medium text-fg-2">方法</legend>
        {presetsProblem && <InlineAlert tone="warn">{presetsProblem}</InlineAlert>}
        {presets.map((item) => (
          <MethodCard
            key={item.id}
            group={group}
            name={item.name}
            summary={item.summary}
            checked={form.method?.kind === 'preset' && form.method.id === item.id}
            onSelect={() => choose({ kind: 'preset', id: item.id })}
          />
        ))}
        {form.method?.kind === 'strategy' && (
          <MethodCard
            group={group}
            name={`策略文件：${strategy?.name ?? form.method.id}`}
            summary={strategy?.description?.split('\n')[0] || 'Python 策略，参数按它自己的名字填写'}
            checked
            onSelect={() => undefined}
          />
        )}
        {errors.method && <p className="text-table text-warn">{errors.method}</p>}
      </fieldset>

      {params.length > 0 && (
        <div className="flex min-w-0 flex-col gap-3 border-l-2 border-line pl-3" role="group" aria-label={`${preset?.name ?? strategy?.name ?? ''}的参数`}>
          {params.map((param) => {
            const key = `param:${param.key}` as const
            const hint = [conversions[param.key], paramHint(param)].filter(Boolean).join('；')
            const value = form.params[param.key] ?? ''
            if (isDateParam(param)) {
              return (
                <Field key={param.key} label={param.label} hint={hint} error={errors[key]}>
                  <DateInput value={value} max={today} onChange={(event) => setParam(param.key, event.target.value)} />
                </Field>
              )
            }
            if (form.method?.kind === 'strategy' && typeof param.default !== 'number') {
              return (
                <Field key={param.key} label={param.label} hint={param.default !== null ? `默认 ${String(param.default)}` : undefined} error={errors[key]}>
                  <Input value={value} placeholder={param.default !== null ? String(param.default) : ''} onChange={(event) => setParam(param.key, event.target.value)} />
                </Field>
              )
            }
            return (
              <Field key={param.key} label={param.label} hint={hint} error={errors[key]}>
                <NumberInput
                  value={value}
                  onValueChange={(text) => setParam(param.key, text)}
                  placeholder={param.default !== null && param.default !== undefined ? String(param.default) : ''}
                  suffix={paramUnit(param)}
                  step={param.step ?? undefined}
                  min={param.min ?? undefined}
                  allowNegative={form.method?.kind === 'strategy'}
                />
              </Field>
            )
          })}
        </div>
      )}

      <Field label="区间" hint={form.period === 'custom' ? '结束日期留空表示到最新一根完整 K 线' : `${range.start} 至 ${range.end}（结束于最新一根完整 K 线）`}>
        <SegmentedControl<PeriodPreset>
          options={PERIODS}
          value={form.period}
          onChange={(period) => set(period === 'custom' && !form.start
            ? { period, start: periodRange('3y', today).start, end: '' }
            : { period })}
        />
      </Field>
      {form.period === 'custom' && (
        <div className="grid grid-cols-2 gap-3">
          <Field label="开始日期" required error={errors.start}>
            <DateInput value={form.start} max={today} onChange={(event) => set({ start: event.target.value })} />
          </Field>
          <Field label="结束日期" error={errors.end}>
            <DateInput value={form.end} max={today} onChange={(event) => set({ end: event.target.value })} />
          </Field>
        </div>
      )}

      <div className="flex min-w-0 flex-col gap-2">
        <p className="text-table text-fg-2">
          {defaults.data ? `默认假设：${defaults.data.assumptions_zh}` : '默认假设：按标的所在市场的费率、每手股数和指数；滑点 0.05%，次日开盘成交，前复权。'}
        </p>
        {changed.length > 0 && <p className="text-table text-fg">已修改：{changed.join('，')}</p>}
        <Disclosure open={showAdvanced} onToggle={() => setAdvanced(!showAdvanced)} controls={advancedId}>
          {showAdvanced ? '收起高级设置' : '修改（高级设置）'}
        </Disclosure>
        <div id={advancedId} hidden={!showAdvanced} className={cx('min-w-0 flex-col gap-3 border-l-2 border-line pl-3', showAdvanced ? 'flex' : 'hidden')}>
          <Field label="本金" hint={defaults.data ? `留空为 ${fmtMoney(defaults.data.capital, defaults.data.currency, { exact: true, digits: 0 })}` : '留空为 100,000（该市场货币）'} error={errors.capital}>
            <MoneyInput currency={currency ?? 'CNY'} value={form.capital} onValueChange={(capital) => set({ capital })} placeholder="100000" />
          </Field>
          <p className="text-table text-fg-3">{feeDefaultsLine(defaults.data)}</p>
          <div className="grid grid-cols-2 gap-3">
            <Field label="买入佣金率（只含佣金）" error={errors.commissionBuy}>
              <NumberInput value={form.commissionBuy} onValueChange={(commissionBuy) => set({ commissionBuy })} suffix="%"
                placeholder={fractionToPercentText(defaults.data?.commission.rate) || '默认'} />
            </Field>
            <Field label="卖出佣金率（只含佣金）" error={errors.commissionSell}>
              <NumberInput value={form.commissionSell} onValueChange={(commissionSell) => set({ commissionSell })} suffix="%"
                placeholder={fractionToPercentText(defaults.data?.commission.rate) || '默认'} />
            </Field>
          </div>
          <Field label="每笔最低佣金" error={errors.minCommission}>
            <MoneyInput currency={currency ?? 'CNY'} value={form.minCommission} onValueChange={(minCommission) => set({ minCommission })}
              placeholder={defaults.data ? String(defaults.data.commission.min) : '默认'} />
          </Field>
          <Field label="滑点（每边）" hint="买入按开盘价加这么多，卖出按开盘价减这么多" error={errors.slippage}>
            <NumberInput value={form.slippage} onValueChange={(slippage) => set({ slippage })} suffix="%"
              placeholder={fractionToPercentText(defaults.data?.slippage_pct) || '0.05'} />
          </Field>
          <Field label="每手股数" hint={defaults.data ? `留空按市场规则：${defaults.data.lot.note || `最少 ${defaults.data.lot.minimum} 股${defaults.data.lot.assumed ? '（假设，请核对）' : ''}`}` : '留空按市场规则'} error={errors.lotSize}>
            <NumberInput value={form.lotSize} onValueChange={(lotSize) => set({ lotSize })} suffix="股" placeholder="按市场" />
          </Field>
          <Field label="复权" hint={form.adjust === 'none' ? '不复权：现金分红不计，送转会显示成下跌；有疑似除权跳空时会拒绝运行' : '前复权：分红、送转视为再投入'} error={errors.adjust}>
            <Select
              value={form.adjust}
              onChange={(event) => set({ adjust: event.target.value as FormState['adjust'] })}
              options={[{ value: 'qfq', label: '前复权（默认）' }, { value: 'hfq', label: '后复权' }, { value: 'none', label: '不复权（不推荐）' }]}
            />
          </Field>
          {form.adjust === 'none' && (
            <Checkbox
              checked={form.allowUnadjusted}
              onChange={(event) => set({ allowUnadjusted: event.target.checked })}
              label="有疑似除权跳空也运行"
              description="结果会把除权当成真实涨跌，并带上提醒"
            />
          )}
          <Field label="对比指数" hint={form.benchmark === 'auto' ? `按市场：${defaults.data?.benchmark?.name ?? defaults.data?.benchmark?.ticker ?? (defaults.data?.demo ? '演示模式没有指数行情' : 'A股沪深300、港股恒生、美股标普500')}` : undefined} error={errors.benchmark}>
            <Select
              value={form.benchmark}
              onChange={(event) => set({ benchmark: event.target.value as FormState['benchmark'] })}
              options={[{ value: 'auto', label: '按市场自动' }, { value: 'none', label: '不对比指数' }, { value: 'custom', label: '其他指数' }]}
            />
          </Field>
          {form.benchmark === 'custom' && (
            <Field label="指数代码" hint="如 IDX:000905（中证500）、IDX:HSTECH" error={errors.benchmark}>
              <Input value={form.benchmarkCode} onChange={(event) => set({ benchmarkCode: event.target.value })} placeholder="IDX:000905" autoComplete="off" spellCheck={false} />
            </Field>
          )}
        </div>
      </div>

      {errors.form && <InlineAlert tone="warn" title="没有开始回测">{errors.form}</InlineAlert>}
      <Button type="submit" variant="primary" icon={Play} loading={running} className="w-full">开始回测</Button>
    </form>
  )
}
