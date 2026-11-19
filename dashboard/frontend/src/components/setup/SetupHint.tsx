// Today's link to the setup wizard while the ledger is empty: on the first visit (full mode, no opening
// balance, wizard neither completed nor dismissed) it opens /setup once per browser tab, and it says when
// an old holdings file is waiting to be migrated. Rendered by Today's empty state only; it never hides
// Today: an unread status, the demo, research mode or a dismissed wizard leave the page as it is.
import { useEffect } from 'react'
import { Link, useNavigate } from 'react-router-dom'
import { useQuery } from '@tanstack/react-query'
import { InlineAlert, buttonClass } from '@/components/ui'
import { SETUP_STATUS_KEY, fetchSetupStatus } from '@/services/setup'
import { alreadyRedirected, rememberRedirect, sessionStore, shouldRedirect } from '@/features/setup/steps'
import { useAppMode } from '@/components/shell/useAppMode'

export function SetupHint() {
  const { mode, unknown } = useAppMode()
  const navigate = useNavigate()
  const status = useQuery({
    queryKey: [...SETUP_STATUS_KEY, 'today'],
    queryFn: () => fetchSetupStatus(false),
    enabled: mode !== undefined && mode !== 'lab',
    staleTime: 30_000,
    retry: false,
  })
  const data = status.data
  useEffect(() => {
    const storage = sessionStore()
    if (shouldRedirect({ mode, unknown, status: data, alreadyRedirected: alreadyRedirected(storage) })) {
      rememberRedirect(storage)
      navigate('/setup')
    }
  }, [mode, unknown, data, navigate])

  const legacy = data?.items.find((item) => item.id === 'legacy')
  if (!legacy || legacy.state !== 'todo') return null
  return (
    <InlineAlert tone="info" title="发现旧版持仓记录">
      <p className="break-words">{legacy.detail}</p>
      <Link to="/setup?step=account&start=legacy" className={`${buttonClass({ size: 'sm' })} mt-2`}>迁移到账本</Link>
    </InlineAlert>
  )
}
