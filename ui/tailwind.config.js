/**
 * Semantic tokens only (FR-064). Components never name a raw colour, so the
 * light and dark themes are two sets of CSS variable values rather than two
 * sets of class names — a component written once renders correctly in both.
 */
export default {
  content: ['./index.html', './src/**/*.{ts,tsx}'],
  theme: {
    extend: {
      colors: {
        canvas: 'rgb(var(--canvas) / <alpha-value>)',
        surface: 'rgb(var(--surface) / <alpha-value>)',
        raised: 'rgb(var(--raised) / <alpha-value>)',
        line: 'rgb(var(--line) / <alpha-value>)',
        hairline: 'rgb(var(--hairline) / <alpha-value>)',
        ink: 'rgb(var(--ink) / <alpha-value>)',
        muted: 'rgb(var(--muted) / <alpha-value>)',
        faint: 'rgb(var(--faint) / <alpha-value>)',
        accent: 'rgb(var(--accent) / <alpha-value>)',
        info: 'rgb(var(--info) / <alpha-value>)',
        danger: 'rgb(var(--danger) / <alpha-value>)',
        'accent-wash': 'rgb(var(--accent-wash) / <alpha-value>)',
        'info-wash': 'rgb(var(--info-wash) / <alpha-value>)',
        'danger-wash': 'rgb(var(--danger-wash) / <alpha-value>)',
      },
      fontFamily: {
        sans: ['Calibri', 'Segoe UI', 'system-ui', 'sans-serif'],
        mono: ['Consolas', 'ui-monospace', 'monospace'],
      },
      fontSize: {
        micro: ['0.625rem', { lineHeight: '0.875rem' }],
        tiny: ['0.6875rem', { lineHeight: '1rem' }],
      },
    },
  },
  plugins: [],
};
