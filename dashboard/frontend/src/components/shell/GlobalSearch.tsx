// The global ticker search in the header (plan EN-2): ⌘K or / focuses it, type a code or a name, pick
// a result and the stock drawer opens. From 1024 px it is a field in the header; below that an icon that
// opens the same field in a dialog. It also hosts the stock drawer and the alert dialog, so that any page
// can open them (`openStockDrawer`, `openNewAlert`).
import { useState } from 'react'
import { Search } from 'lucide-react'
import { Dialog, IconButton } from '@/components/ui'
import { InstrumentCombobox } from '@/components/inputs/InstrumentCombobox'
import { MarketOverlays } from '@/components/market/MarketOverlays'
import { openStockDrawer } from '@/components/market/stockDrawerStore'
import type { InstrumentResult } from '@/lib/instrument'

export const SEARCH_ID = 'global-search'

/** Focuses the search field (keyboard shortcut ⌘K or /); on narrow screens opens the search sheet. */
export function focusSearch() {
  for (const id of [SEARCH_ID, `${SEARCH_ID}-icon`]) {
    const element = document.getElementById(id)
    if (element && element.getClientRects().length > 0) {
      element.focus()
      if (id !== SEARCH_ID) element.click()
      return
    }
  }
}

const PLACEHOLDER = '搜索代码 / 名称'

function Shortcut() {
  const mac = typeof navigator !== 'undefined' && /Mac|iPhone|iPad/.test(navigator.platform)
  return <kbd className="rounded border border-line px-1 font-sans text-caption text-fg-3">{mac ? '⌘K' : 'Ctrl K'}</kbd>
}

export function GlobalSearch() {
  const [text, setText] = useState('')
  const [sheet, setSheet] = useState(false)
  const [sheetText, setSheetText] = useState('')
  const pick = (choice: InstrumentResult) => {
    setText('')
    setSheetText('')
    setSheet(false)
    if (choice.ticker) openStockDrawer(choice.ticker, choice.name)
  }
  return (
    <>
      <span className="hidden lg:inline-flex">
        <InstrumentCombobox
          id={SEARCH_ID}
          value={text}
          onValueChange={setText}
          onPick={pick}
          pickFirstOnEnter
          onEscape={() => (text ? setText('') : (document.activeElement as HTMLElement | null)?.blur())}
          size="sm"
          ariaLabel={PLACEHOLDER}
          placeholder={PLACEHOLDER}
          prefix={<Search size={14} aria-hidden />}
          suffix={text ? undefined : <Shortcut />}
          className="w-56"
          listMinWidth={400}
          align="end"
        />
      </span>
      <span className="lg:hidden">
        <IconButton id={`${SEARCH_ID}-icon`} icon={Search} label={PLACEHOLDER} tipSide="bottom" tipAlign="end" onClick={() => setSheet(true)} />
      </span>
      <Dialog open={sheet} onClose={() => setSheet(false)} title="搜索代码 / 名称" description="选中后打开详情：行情、日线、持仓和提醒。">
        <InstrumentCombobox
          value={sheetText}
          onValueChange={setSheetText}
          onPick={pick}
          pickFirstOnEnter
          ariaLabel={PLACEHOLDER}
          placeholder="代码或名称，如 600519 / 茅台 / 00700"
          prefix={<Search size={16} aria-hidden />}
        />
      </Dialog>
      <MarketOverlays />
    </>
  )
}
