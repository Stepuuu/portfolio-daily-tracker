// The application frame around every page: navigation (sidebar, icon rail or bottom bar), the header,
// the demo and connection banners, the page itself in the one scrolling <main>, and the assistant dock.
// It also owns the overlays any page can open (记一笔, 待确认, 快捷键), the keyboard shortcuts and the one
// <Toaster/>. Legacy pages render inside unchanged.
import { Suspense, useEffect, type CSSProperties, type ReactNode } from 'react'
import { Navigate, Outlet, useLocation } from 'react-router-dom'
import { Toaster } from '@/components/ui'
import { EntryDialog } from '@/components/ledger/EntryDialog'
import { LedgerProposalReview } from '@/components/ledger/ProposalReview'
import { allowedIn, documentTitle, homePath } from '@/lib/navigation'
import { searchShortcut } from '@/features/market/searchShortcut'
import { sections, type PageRoute } from '@/routes'
import { AssistantDock } from './AssistantDock'
import { ConnectionBanner } from './ConnectionBanner'
import { DemoBanner } from './DemoBanner'
import { focusSearch } from './GlobalSearch'
import { Header } from './Header'
import { MobileNav } from './MobileNav'
import { PendingTray } from './PendingTray'
import { SectionTabs } from './SectionTabs'
import { ShortcutsDialog } from './ShortcutsDialog'
import { Sidebar } from './Sidebar'
import { useShell } from './shellStore'
import { useAppMode, useModeFollowsBackend } from './useAppMode'
import { useNavModel } from './useNavModel'

export function PageLoading() {
  return <div className="p-6 text-body text-fg-3" aria-busy="true">正在加载…</div>
}

/** A page of the route table, sent to the mode's first page when the mode does not show it. */
export function RoutePage({ route, children }: { route: PageRoute; children: ReactNode }) {
  const { mode } = useAppMode()
  if (mode && !allowedIn(route, mode)) return <Navigate to={homePath(sections, mode)} replace />
  return <Suspense fallback={<PageLoading />}>{children}</Suspense>
}

function typing(target: EventTarget | null): boolean {
  if (!(target instanceof HTMLElement)) return false
  return target.isContentEditable || ['INPUT', 'TEXTAREA', 'SELECT'].includes(target.tagName)
}

function useShortcuts() {
  const { mode, lab, readOnly } = useAppMode()
  useEffect(() => {
    const onKey = (event: KeyboardEvent) => {
      if (event.defaultPrevented || document.querySelector('dialog[open]')) return
      // ⌘K and /: never while typing in a field or composing (features/market/searchShortcut.ts).
      const search = searchShortcut(event)
      if (search !== 'ignore') {
        event.preventDefault()
        if (search === 'focus') focusSearch()
        return
      }
      const command = event.metaKey || event.ctrlKey
      if (command || event.altKey || event.isComposing || typing(event.target)) return
      if (event.key === '?') {
        useShell.getState().setShortcutsOpen(true)
      } else if ((event.key === 'n' || event.key === 'N') && mode && !lab && !readOnly) {
        event.preventDefault()
        useShell.getState().openEntry()
      }
    }
    document.addEventListener('keydown', onKey)
    return () => document.removeEventListener('keydown', onKey)
  }, [mode, lab, readOnly])
}

export function AppShell() {
  const { pathname } = useLocation()
  const { current } = useNavModel()
  const { lab } = useAppMode()
  const entry = useShell((state) => state.entry)
  const closeEntry = useShell((state) => state.closeEntry)
  const trayOpen = useShell((state) => state.trayOpen)
  const setTrayOpen = useShell((state) => state.setTrayOpen)
  useShortcuts()
  // The backend may come back in another mode (demo → full or the reverse): read it again then.
  useModeFollowsBackend()

  useEffect(() => {
    document.title = documentTitle(current)
  }, [current])

  // A new page starts at its top.
  useEffect(() => {
    document.getElementById('main')?.scrollTo(0, 0)
  }, [pathname])

  // Sticky table headers stick to the top of <main>, the page's scroll container.
  const mainStyle = { '--sticky-top': '0px' } as CSSProperties

  return (
    <div className="flex h-[100dvh] overflow-hidden bg-bg font-sans">
      <Sidebar />
      <div className="flex min-w-0 flex-1 flex-col">
        <Header />
        <DemoBanner />
        {!lab && <ConnectionBanner />}
        <div className="relative flex min-h-0 flex-1">
          <main id="main" style={mainStyle} className="min-w-0 flex-1 overflow-auto">
            <SectionTabs />
            <div className="p-4 md:p-6">
              <Outlet />
            </div>
          </main>
          {!lab && <AssistantDock />}
        </div>
        <MobileNav />
      </div>
      {!lab && (
        <>
          <EntryDialog open={entry !== null} onClose={closeEntry} draft={entry} />
          <PendingTray open={trayOpen} onClose={() => setTrayOpen(false)} ledgerReview={LedgerProposalReview} />
        </>
      )}
      <ShortcutsDialog />
      <Toaster />
    </div>
  )
}
