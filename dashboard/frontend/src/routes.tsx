// The route table: six destinations, their pages, redirects and hidden routes (plan 2.2). App.tsx builds
// the router from it and the sidebar, icon rail and bottom bar draw the navigation from it.
//
// Adding a page is one line in its section's `pages` (a page appears in the navigation only once its file
// exists), e.g. in `market`:
//   { path: '/alerts', label: '提醒', element: lazy(() => import('./pages/Alerts')), modes: DESK },
// A moved page adds one line to `redirects`. Keep this file free of JSX: tests/routes.test.mjs loads it.
import { lazy, type ComponentType, type LazyExoticComponent } from 'react'
import { BookOpen, CalendarCheck, CandlestickChart, FlaskConical, Home, Settings, type LucideIcon } from 'lucide-react'
import type { Mode } from './lib/navigation'

export type { Mode }

export interface PageRoute {
  /** Absolute path; `/*` at the end lets the page own the paths below it. */
  path: string
  /** Tab and title text. */
  label: string
  element: LazyExoticComponent<ComponentType>
  /** Modes that show the page; every mode when omitted. Elsewhere the URL goes to the mode's first page. */
  modes?: Mode[]
  /** Rendered without the app shell (full-screen pages such as the UI gallery or a setup wizard). */
  bare?: boolean
}

export interface NavSection {
  id: 'today' | 'market' | 'ledger' | 'review' | 'research' | 'settings'
  label: string
  icon: LucideIcon
  /** The first page is where the section's navigation entry leads; more than one page shows tabs. */
  pages: PageRoute[]
  /** Phones: an entry of its own in the bottom bar, or under 更多. */
  mobile: 'primary' | 'more'
}

/** The daily desk: everything that reads or writes the portfolio. Not in research-only mode. */
const DESK: Mode[] = ['full', 'demo']

export const sections: NavSection[] = [
  {
    id: 'today', label: '今日', icon: Home, mobile: 'primary', pages: [
      { path: '/', label: '今日', element: lazy(() => import('./pages/Today')), modes: DESK },
    ],
  },
  {
    id: 'market', label: '行情', icon: CandlestickChart, mobile: 'primary', pages: [
      { path: '/market', label: '自选', element: lazy(() => import('./pages/market/Watchlist')), modes: DESK },
      { path: '/alerts', label: '提醒', element: lazy(() => import('./pages/market/Alerts')), modes: DESK },
    ],
  },
  {
    id: 'ledger', label: '账本', icon: BookOpen, mobile: 'more', pages: [
      { path: '/ledger', label: '流水', element: lazy(() => import('./pages/Ledger')), modes: DESK },
      { path: '/ledger/positions', label: '持仓与成本', element: lazy(() => import('./pages/ledger/Positions')), modes: DESK },
      { path: '/ledger/import', label: '导入', element: lazy(() => import('./pages/ledger/Import')), modes: DESK },
    ],
  },
  {
    id: 'review', label: '计划与复盘', icon: CalendarCheck, mobile: 'more', pages: [
      { path: '/review', label: '今日简报', element: lazy(() => import('./pages/review/Brief')), modes: DESK },
      { path: '/plans', label: '计划', element: lazy(() => import('./pages/review/Plans')), modes: DESK },
      { path: '/tracker', label: '组合走势', element: lazy(() => import('./pages/PortfolioTracker')), modes: DESK },
      { path: '/notes', label: '笔记', element: lazy(() => import('./pages/review/Notes')), modes: DESK },
    ],
  },
  {
    id: 'research', label: '研究', icon: FlaskConical, mobile: 'more', pages: [
      { path: '/lab', label: '工作台', element: lazy(() => import('./pages/ResearchLab')) },
      { path: '/backtest', label: '回测', element: lazy(() => import('./pages/research/Backtest')) },
    ],
  },
  {
    id: 'settings', label: '设置', icon: Settings, mobile: 'more', pages: [
      // `/settings` opens the mode's first section; the sections themselves are the hidden route below.
      { path: '/settings', label: '设置', element: lazy(() => import('./pages/settings/Settings')) },
    ],
  },
]

/**
 * Old paths (v3.2 pages and links) and where they live now; the query and hash travel with them. `/tracker`
 * stays a page until 组合走势 moves to `/history` (UI-4).
 */
export const redirects: { from: string; to: string }[] = [
  { from: '/portfolio', to: '/ledger' },
  { from: '/dashboard', to: '/' },
  { from: '/memory', to: '/settings/preferences' },
  { from: '/help', to: '/settings/about' },
  { from: '/settings/cash', to: '/ledger' },
  { from: '/settings/help', to: '/settings/about' },
  { from: '/settings/memory', to: '/settings/preferences' },
]

/** Routes outside the navigation. The UI gallery exists in development builds only. */
export const hiddenRoutes: PageRoute[] = [
  { path: '/setup', label: '设置向导', element: lazy(() => import('./pages/setup/Setup')) },
  // A section of 设置 (features/settings/sections.ts); the navigation's entry is `/settings`.
  { path: '/settings/:section', label: '设置', element: lazy(() => import('./pages/settings/Settings')) },
  ...(import.meta.env.DEV
    ? [{ path: '/__ui/*', label: '组件库', element: lazy(() => import('./pages/dev/UiGallery')), bare: true }]
    : []),
]

/** Every page in the navigation, in order. */
export const navPages: PageRoute[] = sections.flatMap((section) => section.pages)
