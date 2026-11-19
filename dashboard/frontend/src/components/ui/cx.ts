import { clsx, type ClassValue } from 'clsx'
import { extendTailwindMerge } from 'tailwind-merge'

// tailwind-merge must know the custom font sizes and shadow, or it would treat `text-body` as a colour
// and drop it next to `text-fg`.
const merge = extendTailwindMerge({
  extend: {
    classGroups: {
      'font-size': [{ text: ['caption', 'table', 'body', 'title', 'page', 'figure'] }],
      shadow: [{ shadow: ['overlay'] }],
    },
  },
})

/** Joins class names; a later class wins over an earlier one of the same kind (`px-3` then `px-4`). */
export function cx(...inputs: ClassValue[]): string {
  return merge(clsx(inputs))
}

/** Keyboard focus: a 2 px accent outline just outside the element. */
export const focusRing =
  'focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-accent'

/** Keyboard focus drawn inside the element, for things that sit flush against an edge (tabs, rows). */
export const focusRingInset =
  'focus-visible:outline focus-visible:outline-2 focus-visible:-outline-offset-2 focus-visible:outline-accent'

/** Numbers: tabular, lining figures so columns line up. */
export const numeric = 'tabular-nums lining-nums'

/** Space-separated id list for aria-describedby / aria-labelledby; undefined when empty. */
export function joinIds(...ids: (string | false | null | undefined)[]): string | undefined {
  return ids.filter(Boolean).join(' ') || undefined
}
