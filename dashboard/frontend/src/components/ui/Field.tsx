import {
  cloneElement, createContext, isValidElement, useCallback, useContext, useId, useLayoutEffect, useState, type ReactElement,
  type ReactNode,
} from 'react'
import { AlertCircle } from 'lucide-react'
import { cx, joinIds } from './cx'

interface FieldContextValue {
  id: string
  labelId: string
  describedBy?: string
  invalid: boolean
  required: boolean
  /** A control's own refusal of what was typed (e.g. NumberInput); shown as the field's error until cleared. */
  report: (message: string | null) => void
  /** A kit control further down says which id it rendered with, so the label points at it. */
  claim: (id: string) => () => void
}

const FieldContext = createContext<FieldContextValue | null>(null)

const NATIVE_CONTROLS = new Set(['input', 'select', 'textarea', 'button', 'meter', 'output', 'progress'])

interface ControlAria {
  id?: string
  'aria-describedby'?: string
  'aria-invalid'?: boolean | 'true' | 'false' | 'grammar' | 'spelling'
  'aria-labelledby'?: string
  'aria-required'?: boolean | 'true' | 'false'
}

/**
 * Props that connect a control to its Field (id for the label, hint and error descriptions, invalid
 * and required state). Kit controls call this; a custom control inside a Field can too. Required is
 * announced with aria-required, not the `required` attribute, so the browser's own validation bubble
 * never competes with the Field's error line.
 */
export function useFieldControl<P extends ControlAria>(props: P): P {
  const field = useContext(FieldContext)
  const claim = field?.claim
  // A control with its own id keeps it; otherwise it takes the Field's. Either way the label points at
  // the id the control really has (see Field), also when a wrapper with an id of its own sits between.
  const id = props.id ?? field?.id
  useLayoutEffect(() => {
    if (!claim || !id) return
    return claim(id)
  }, [claim, id])
  if (!field) return props
  return {
    ...props,
    id,
    'aria-describedby': joinIds(field.describedBy, props['aria-describedby']),
    'aria-invalid': props['aria-invalid'] ?? (field.invalid || undefined),
    'aria-required': props['aria-required'] ?? (field.required || undefined),
  }
}

/**
 * For controls that refuse input themselves (NumberInput): shows `message` as the surrounding Field's
 * error, `null` clears it. Undefined outside a Field, where the control shows the message itself.
 */
export function useFieldReport(): ((message: string | null) => void) | undefined {
  return useContext(FieldContext)?.report
}

/** The label id of the surrounding Field, for controls that are not inputs (radio groups). */
export function useFieldLabelId(): string | undefined {
  return useContext(FieldContext)?.labelId
}

export interface FieldProps {
  label: ReactNode
  /** Constraints and help, one short line. Hidden while there is an error. */
  hint?: ReactNode
  /** What is wrong and how to fix it. */
  error?: ReactNode
  required?: boolean
  /** Hide the label visually (it is still read by screen readers). */
  labelHidden?: boolean
  className?: string
  children: ReactNode
}

/**
 * Label, control, hint or error, wired for screen readers. Wrap exactly one control. A control that brings
 * its own `id` keeps it and the label points at it, whether it is the direct child or a kit control
 * further down (inside a wrapper element or a component of the page's own, whatever id the wrapper has);
 * otherwise the Field gives it one. A wrapper's id is never handed to the control inside it.
 */
export function Field({ label, hint, error, required = false, labelHidden = false, className, children }: FieldProps) {
  const generated = useId()
  const [claimed, setClaimed] = useState<string | null>(null)
  const claim = useCallback((id: string) => {
    setClaimed(id)
    return () => setClaimed((current) => (current === id ? null : current))
  }, [])
  const native = isValidElement(children) && typeof children.type === 'string' && NATIVE_CONTROLS.has(children.type)
  const nativeId = native ? (children.props as ControlAria).id : undefined
  // A component child with an id of its own that wires nothing itself (a date picker passing it to its
  // input): the label points at that id until a kit control inside says otherwise.
  const componentId = isValidElement(children) && typeof children.type !== 'string' ? (children.props as ControlAria).id : undefined
  // What the controls inside take when they bring no id: never a wrapper's own.
  const id = nativeId ?? generated
  const target = nativeId ?? claimed ?? componentId ?? generated
  const labelId = `${generated}-label`
  const hintId = `${generated}-hint`
  const errorId = `${generated}-error`
  const [reported, report] = useState<string | null>(null)
  // A refusal of what was just typed or pasted is the most recent news, so it shows over the form's error.
  const shownError = reported ?? error
  const describedBy = shownError ? errorId : hint ? hintId : undefined
  const value: FieldContextValue = { id, labelId, describedBy, invalid: Boolean(shownError), required, report, claim }
  // A native control gets the wiring directly; kit controls (also inside a layout wrapper) read it from the
  // context. A wrapper element itself is never wired: the label must point at the control, not the div.
  const control =
    native
      ? cloneElement(children as ReactElement<ControlAria>, {
        id,
        'aria-describedby': describedBy,
        'aria-invalid': shownError ? true : undefined,
        'aria-required': required || undefined,
      })
      : children
  return (
    <div className={cx('flex min-w-0 flex-col gap-1', className)}>
      <label id={labelId} htmlFor={target} className={cx('text-table font-medium text-fg-2', labelHidden && 'sr-only')}>
        {label}
        {required && <span aria-hidden className="ml-0.5 text-fg-3">*</span>}
      </label>
      <FieldContext.Provider value={value}>{control}</FieldContext.Provider>
      {shownError ? (
        <p id={errorId} role={reported ? 'alert' : undefined} className="flex items-start gap-1 text-table text-warn">
          <AlertCircle size={14} className="mt-[3px] shrink-0" aria-hidden />
          <span>{shownError}</span>
        </p>
      ) : hint ? (
        <p id={hintId} className="text-table text-fg-3">{hint}</p>
      ) : null}
    </div>
  )
}
