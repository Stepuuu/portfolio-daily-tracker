import { useMemo } from 'react'
import { useLocation } from 'react-router-dom'
import { sections, type NavSection, type PageRoute } from '@/routes'
import { findPage, visibleSections } from '@/lib/navigation'
import { useAppMode } from './useAppMode'

/**
 * The sections the current mode shows and the one the URL is in. Until the mode is known, nothing is
 * shown, so research-only mode never flashes the desk's sections.
 */
export function useNavModel(): { sections: NavSection[]; current: { section: NavSection; page: PageRoute } | null; ready: boolean } {
  const { mode } = useAppMode()
  const { pathname } = useLocation()
  return useMemo(() => {
    const visible = mode ? visibleSections<PageRoute, NavSection>(sections, mode) : []
    return { sections: visible, current: findPage<PageRoute, NavSection>(mode ? visible : sections, pathname), ready: Boolean(mode) }
  }, [mode, pathname])
}
