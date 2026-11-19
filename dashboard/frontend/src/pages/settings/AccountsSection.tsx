// 设置 › 账户与费率: each account's fee profile, which 记一笔 uses to prefill the fee. A-shares: 佣金率 (万分之),
// 最低佣金, 免五 (statutory 过户费 and 印花税 are added by the estimator). Hong Kong and US: an optional rate,
// minimum and fixed fee per side, in the trade currency; without one the fee is typed per trade. A profile
// is a setting and saves directly (no preview). Read-only (fields and 保存) in demo mode and while the app mode
// is unknown. The form checks the server's bounds (rate at most 100‱, 6 decimals in 万分之; minimum and fixed
// fee at most 100,000 with 4 decimals) and shows the saved figures with all their digits.
import { useState, type FormEvent } from 'react'
import { Link } from 'react-router-dom'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import {
  Badge, Button, Checkbox, EmptyState, Field, InlineAlert, Input, MoneyInput, NumberInput, Panel, Select, Skeleton,
  cx, focusRing, numeric, toast,
} from '@/components/ui'
import { getApiErrorMessage } from '@/services/api'
import { PROFILES_KEY, fetchProfiles, saveProfile } from '@/services/ledger'
import {
  formToProfile, profileSummary, profileToForm, type MarketForm, type ProfileErrors, type ProfileForm, type SideForm,
} from '@/features/ledger/fees'
import type { FeeProfile } from '@/features/ledger/types'
import { useAppMode } from '@/components/shell/useAppMode'
import { useLedger } from '@/components/ledger/useLedger'

const MARKET = { hk: { label: '港股', currency: 'HKD' }, us: { label: '美股', currency: 'USD' } } as const

function SideRow({ label, side, currency, prefix, errors, onChange }: {
  label: string; side: SideForm; currency: string; prefix: string; errors: ProfileErrors; onChange: (side: SideForm) => void
}) {
  return (
    <div className="grid gap-3 sm:grid-cols-[3rem_repeat(3,minmax(0,1fr))] sm:items-start">
      <span className="pt-1.5 text-table text-fg-2">{label}</span>
      <Field label={`${label}费率`} labelHidden error={errors[`${prefix}.rate`]}>
        <NumberInput value={side.rate} onValueChange={(rate) => onChange({ ...side, rate })} suffix="‱" placeholder="费率" />
      </Field>
      <Field label={`${label}最低收费`} labelHidden error={errors[`${prefix}.min`]}>
        <MoneyInput currency={currency} value={side.min} onValueChange={(min) => onChange({ ...side, min })} placeholder="最低" />
      </Field>
      <Field label={`${label}固定费用`} labelHidden error={errors[`${prefix}.fixed`]}>
        <MoneyInput currency={currency} value={side.fixed} onValueChange={(fixed) => onChange({ ...side, fixed })} placeholder="每笔固定" />
      </Field>
    </div>
  )
}

function MarketFields({ market, value, errors, onChange }: {
  market: 'hk' | 'us'; value: MarketForm; errors: ProfileErrors; onChange: (value: MarketForm) => void
}) {
  const { label, currency } = MARKET[market]
  return (
    <fieldset className="flex min-w-0 flex-col gap-3 border-t border-line pt-4">
      <legend className="sr-only">{label}费率</legend>
      <Checkbox
        checked={value.enabled}
        onChange={(event) => onChange({ ...value, enabled: event.target.checked })}
        label={`设置${label}费率`}
        description={`每笔 = max(费率 × 成交额, 最低收费) + 固定费用，${currency} 计；不设置时，${label}的费用按成交单逐笔填写。`}
      />
      {value.enabled && (
        <div className="flex flex-col gap-3 sm:pl-6">
          <SideRow label="买入" side={value.buy} currency={currency} prefix={`${market}.buy`} errors={errors} onChange={(buy) => onChange({ ...value, buy })} />
          <SideRow label="卖出" side={value.sell} currency={currency} prefix={`${market}.sell`} errors={errors} onChange={(sell) => onChange({ ...value, sell })} />
          <p className="text-table text-fg-3">费率按万分之填写（万3 填 3，最高 100）；最低和固定费用最多 100,000、4 位小数；某一边留空表示那一边不估算。</p>
        </div>
      )}
    </fieldset>
  )
}

function ProfileEditor({ account, profile }: { account: string; profile: FeeProfile }) {
  const client = useQueryClient()
  const { readOnlyReason } = useAppMode()
  const [form, setForm] = useState<ProfileForm>(() => profileToForm(profile))
  const [errors, setErrors] = useState<ProfileErrors>({})
  const save = useMutation({
    mutationFn: (body: Parameters<typeof saveProfile>[1]) => saveProfile(account, body),
    onSuccess: async (saved) => {
      toast.success(`已保存「${account}」的费率：${profileSummary(saved)}`)
      await Promise.all([
        client.invalidateQueries({ queryKey: PROFILES_KEY }),
        client.invalidateQueries({ queryKey: ['accounts', 'fee-estimate'] }),
      ])
    },
  })
  const patch = (next: Partial<ProfileForm>) => setForm((current) => ({ ...current, ...next }))
  const submit = (event: FormEvent) => {
    event.preventDefault()
    const { profile: body, errors: found } = formToProfile(form)
    setErrors(found)
    if (body) save.mutate(body)
  }
  return (
    <form onSubmit={submit} noValidate className="flex flex-col gap-4">
      <fieldset disabled={Boolean(readOnlyReason)} className="flex min-w-0 flex-col gap-4">
      <fieldset className="flex min-w-0 flex-col gap-3">
        <legend className="mb-1 text-table font-medium text-fg-2">A 股（股票、ETF）</legend>
        <div className="grid gap-4 sm:grid-cols-[repeat(2,minmax(0,12rem))_auto] sm:items-start">
          <Field label="佣金率" required hint="万分之，万2.5 填 2.5；最高 100" error={errors.commissionRate}>
            <NumberInput value={form.commissionRate} onValueChange={(commissionRate) => patch({ commissionRate })} suffix="‱" />
          </Field>
          <Field label="最低佣金" required={!form.waiveMinimum} hint={form.waiveMinimum ? '免五：不收最低佣金' : '每笔不足时按此收取'} error={errors.commissionMin}>
            <MoneyInput currency="CNY" value={form.commissionMin} onValueChange={(commissionMin) => patch({ commissionMin })} disabled={form.waiveMinimum} />
          </Field>
          <div className="sm:pt-7">
            <Checkbox checked={form.waiveMinimum} onChange={(event) => patch({ waiveMinimum: event.target.checked })} label="免五" />
          </div>
        </div>
        <p className="text-table text-fg-3">过户费和卖出印花税按现行法定费率另计；可转债、REIT 等品种不估算。</p>
      </fieldset>
      <MarketFields market="hk" value={form.hk} errors={errors} onChange={(hk) => patch({ hk })} />
      <MarketFields market="us" value={form.us} errors={errors} onChange={(us) => patch({ us })} />
      <div className="border-t border-line pt-4">
        <Field label="券商" hint="选填，仅作备注" error={errors.broker} className="max-w-xs">
          <Input value={form.broker} onChange={(event) => patch({ broker: event.target.value })} maxLength={40} />
        </Field>
      </div>
      </fieldset>
      {save.error && <InlineAlert tone="warn" title="没有保存">{getApiErrorMessage(save.error)}</InlineAlert>}
      <div>
        <Button type="submit" variant="primary" loading={save.isPending} disabledReason={readOnlyReason}>保存</Button>
      </div>
    </form>
  )
}

export function AccountsSection() {
  const { mode } = useAppMode()
  const desk = mode === 'full' || mode === 'demo'
  const ledger = useLedger()
  const profiles = useQuery({ queryKey: PROFILES_KEY, queryFn: fetchProfiles, enabled: desk })
  const accounts = Object.keys(ledger.data?.accounts ?? {})
  const [chosen, setChosen] = useState('')
  // Research-only mode has no ledger; wait for the mode so it never flashes there.
  if (!desk) return null
  const account = accounts.includes(chosen) ? chosen : accounts[0] ?? ''
  const saved = profiles.data?.items.find((item) => item.account === account)
  const profile = saved ?? (profiles.data ? { ...profiles.data.preset, account } : undefined)

  let body
  if (ledger.isPending || profiles.isPending) {
    body = <Skeleton lines={4} />
  } else if (ledger.isError || profiles.isError) {
    const error = ledger.error ?? profiles.error
    body = (
      <InlineAlert tone="warn" title="费率读取失败" action={<Button size="sm" onClick={() => { void ledger.refetch(); void profiles.refetch() }}>重试</Button>}>
        {getApiErrorMessage(error)}
      </InlineAlert>
    )
  } else if (!accounts.length) {
    body = (
      <EmptyState
        title="账本还没有账户"
        description="在账本中建立账户后，可以在这里设置它的费率。"
        action={<Link to="/ledger" className={cx('rounded-sm text-body text-accent underline-offset-4 hover:underline', focusRing)}>去账本</Link>}
      />
    )
  } else if (profile) {
    body = (
      <div className="flex flex-col gap-4">
        <div className="flex flex-wrap items-end gap-x-4 gap-y-2">
          <Field label="账户" className="w-48">
            <Select value={account} onChange={(event) => setChosen(event.target.value)} options={accounts.map((name) => ({ value: name, label: name }))} />
          </Field>
          <p className={cx('pb-1.5 text-table text-fg-2', numeric)}>
            当前：{profileSummary(profile)}
            {!saved && <Badge className="ml-2">预设</Badge>}
          </p>
        </div>
        <ProfileEditor key={`${account}:${saved?.updated_at ?? 'preset'}`} account={account} profile={profile} />
      </div>
    )
  }
  return (
    <Panel title="账户与费率" description="记一笔时按这里的费率预填费用估算；实际费用以成交单为准。" aria-label="账户与费率">
      {body}
    </Panel>
  )
}

export default AccountsSection
