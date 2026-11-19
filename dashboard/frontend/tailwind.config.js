import plugin from 'tailwindcss/plugin'

// Colour roles read the CSS variables in src/styles/tokens.css, so `bg-up/10` etc. work in both themes.
const token = (name) => `rgb(var(--${name}) / <alpha-value>)`

/** @type {import('tailwindcss').Config} */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  // Only the token roles change between themes; `dark:` is rarely needed.
  darkMode: ['selector', '[data-theme="dark"]'],
  theme: {
    extend: {
      colors: {
        // Legacy palette, still used by the pages that have not moved to the kit.
        primary: {
          50:  '#eff6ff',
          100: '#dbeafe',
          200: '#bfdbfe',
          300: '#93c5fd',
          400: '#60a5fa',
          500: '#3b82f6',
          600: '#2563eb',
          700: '#1d4ed8',
          800: '#1e40af',
          900: '#1e3a8a',
          950: '#172554',
        },
        bg: token('bg'),
        surface: { DEFAULT: token('surface'), 2: token('surface-2'), 3: token('surface-3') },
        line: { DEFAULT: token('border'), input: token('border-input') },
        fg: { DEFAULT: token('text'), 2: token('text-2'), 3: token('text-3') },
        accent: { DEFAULT: token('accent'), fg: token('accent-fg') },
        up: token('up'),
        down: token('down'),
        warn: token('warn'),
      },
      // New names, so the default sizes used by legacy pages do not shift.
      fontSize: {
        caption: ['12px', { lineHeight: '16px' }],
        table: ['13px', { lineHeight: '20px' }],
        body: ['14px', { lineHeight: '22px' }],
        title: ['16px', { lineHeight: '24px', fontWeight: '600' }],
        page: ['20px', { lineHeight: '28px', fontWeight: '600' }],
        figure: ['28px', { lineHeight: '36px', fontWeight: '600', letterSpacing: '-0.01em' }],
      },
      fontFamily: {
        sans: [
          '-apple-system', 'BlinkMacSystemFont', '"Segoe UI"', '"PingFang SC"', '"Hiragino Sans GB"',
          '"Microsoft YaHei"', '"Noto Sans SC"', '"Noto Sans CJK SC"', 'sans-serif',
        ],
      },
      boxShadow: {
        overlay: 'var(--shadow-overlay)',
      },
      keyframes: {
        'ui-fade': { from: { opacity: '0' }, to: { opacity: '1' } },
        'ui-pop': { from: { opacity: '0', transform: 'translateY(4px) scale(0.98)' }, to: { opacity: '1', transform: 'none' } },
        'ui-slide': { from: { opacity: '0', transform: 'translateX(24px)' }, to: { opacity: '1', transform: 'none' } },
        'ui-flash-up': { from: { backgroundColor: 'rgb(var(--up) / 0.1)' }, to: { backgroundColor: 'transparent' } },
        'ui-flash-down': { from: { backgroundColor: 'rgb(var(--down) / 0.1)' }, to: { backgroundColor: 'transparent' } },
      },
      animation: {
        'fade-in': 'ui-fade 120ms ease-out both',
        // Skeletons appear only when loading takes longer than 200 ms.
        'delayed-in': 'ui-fade 160ms ease-out 200ms both',
        // No forwards fill: a filled transform keeps the element a containing block for position: fixed
        // descendants, so a list or menu placed on the screen inside a dialog would be placed in the dialog.
        'pop-in': 'ui-pop 120ms ease-out backwards',
        'slide-in': 'ui-slide 160ms ease-out backwards',
        'flash-up': 'ui-flash-up 800ms ease-out both',
        'flash-down': 'ui-flash-down 800ms ease-out both',
      },
    },
  },
  plugins: [
    // `coarse:` targets touch screens (bigger rows and hit areas).
    plugin(({ addVariant }) => {
      addVariant('coarse', '@media (pointer: coarse)')
    }),
  ],
}
