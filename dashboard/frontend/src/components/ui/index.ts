// The shared UI kit. Pages import from '@/components/ui'; see /__ui (development builds) for every
// component in every state. Colours come from src/styles/tokens.css, numbers from src/lib/format.ts.
export { Button, IconButton, TipBubble, buttonClass, type ButtonProps, type ButtonSize, type ButtonVariant, type IconButtonProps } from './Button'
export { Panel, PageHeader, type PanelProps, type PageHeaderProps } from './Panel'
export { Stat, StatGroup, Badge, type StatProps, type StatTone, type BadgeTone } from './Stat'
export { LongValue, type LongValueProps } from './LongValue'
export { DataTable, type Column, type DataTableProps, type GroupBy } from './DataTable'
export { Field, useFieldControl, useFieldLabelId, useFieldReport, type FieldProps } from './Field'
export {
  Input, NumberInput, MoneyInput, Select, Textarea, Checkbox, DateInput,
  type InputProps, type NumberInputProps, type MoneyInputProps, type SelectProps, type SelectOption, type CheckboxProps,
} from './inputs'
export { SegmentedControl, Tabs, type SegmentedControlProps, type SegmentOption, type TabItem, type TabsProps } from './Tabs'
export { Dialog, Drawer, type DialogProps, type DialogSize } from './Dialog'
export { Popover, type PopoverProps } from './Popover'
export { EmptyState, Skeleton, TableSkeleton, InlineAlert, type EmptyStateProps, type InlineAlertProps } from './feedback'
export { Toaster } from './Toaster'
export { toast } from '@/lib/toast'
export { useTheme, useTokens, useMediaQuery } from './hooks'
export { cx, focusRing, numeric } from './cx'
