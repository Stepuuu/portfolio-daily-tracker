import { useId, type HTMLAttributes, type ReactNode } from 'react'
import { cx } from './cx'
import { Tabs, type TabItem } from './Tabs'

export interface PanelProps extends Omit<HTMLAttributes<HTMLElement>, 'title'> {
  title?: ReactNode
  description?: ReactNode
  /** Buttons on the right of the title. */
  actions?: ReactNode
  /** 'md' pads the content by 16 px; 'none' lets a table run edge to edge. */
  padding?: 'none' | 'md'
  as?: 'section' | 'div'
  children: ReactNode
}

/**
 * The one container: a flat surface with a 1 px border. Do not nest panels; inside a panel, separate
 * groups with headings, spacing or a divider.
 */
export function Panel({
  title, description, actions, padding = 'md', as: Tag = 'section', className, children, ...rest
}: PanelProps) {
  const titleId = useId()
  const hasHeader = Boolean(title || description || actions)
  return (
    <Tag
      aria-labelledby={title && !rest['aria-label'] ? titleId : undefined}
      className={cx(
        'min-w-0 rounded-lg border border-line bg-surface text-fg',
        // Clip (not hidden) keeps a table's corners inside the radius without breaking its sticky header.
        padding === 'none' && 'overflow-clip',
        className,
      )}
      {...rest}
    >
      {hasHeader && (
        <div className={cx('flex flex-wrap items-start justify-between gap-x-4 gap-y-2 px-4 pt-3', padding === 'none' ? 'pb-3' : 'pb-1')}>
          <div className="min-w-0">
            {title && <h2 id={titleId} className="text-title text-fg">{title}</h2>}
            {description && <p className="text-table text-fg-3">{description}</p>}
          </div>
          {actions && <div className="flex shrink-0 flex-wrap items-center gap-2">{actions}</div>}
        </div>
      )}
      <div className={cx(padding === 'md' && 'p-4', padding === 'md' && hasHeader && 'pt-3')}>{children}</div>
    </Tag>
  )
}

export interface PageHeaderProps {
  title: ReactNode
  /** One sentence at most. */
  description?: ReactNode
  /** Page actions, primary last. */
  actions?: ReactNode
  /** Section tabs; give `href` for route tabs. */
  tabs?: TabItem[]
  /** Selected tab for in-page tabs (route tabs follow the URL). */
  tab?: string
  onTabChange?: (id: string) => void
  /** Accessible name of the tab list; defaults to the title when it is text. */
  tabsLabel?: string
  className?: string
}

/** Page title, optional one-line description, actions on the right and section tabs underneath. */
export function PageHeader({ title, description, actions, tabs, tab, onTabChange, tabsLabel, className }: PageHeaderProps) {
  return (
    <header className={cx('flex min-w-0 flex-col gap-3', className)}>
      <div className="flex flex-wrap items-center justify-between gap-x-6 gap-y-3">
        <div className="min-w-0">
          <h1 className="text-page text-fg">{title}</h1>
          {description && <div className="mt-1 text-body text-fg-2">{description}</div>}
        </div>
        {actions && <div className="flex flex-wrap items-center gap-2">{actions}</div>}
      </div>
      {tabs && tabs.length > 0 && (
        <Tabs
          items={tabs}
          value={tab}
          onChange={onTabChange}
          ariaLabel={tabsLabel ?? (typeof title === 'string' ? title : undefined)}
        />
      )}
    </header>
  )
}
