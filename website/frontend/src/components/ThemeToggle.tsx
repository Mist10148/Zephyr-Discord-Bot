import { motion } from 'motion/react'
import { useTheme } from '../lib/theme-context'
import { haptic } from '../lib/haptics'

// A single control that reads its own state from the theme context. Sun/moon glyphs
// are inline SVG so they inherit currentColor and need no icon dependency.
export function ThemeToggle() {
  const { theme, toggle } = useTheme()
  const dark = theme === 'dark'
  return (
    <motion.button
      type="button"
      className="theme-toggle"
      whileTap={{ scale: .9 }}
      transition={{ type: 'spring', stiffness: 400, damping: 30 }}
      onClick={() => { haptic(); toggle() }}
      aria-label={dark ? 'Switch to light appearance' : 'Switch to dark appearance'}
      aria-pressed={dark}
      title={dark ? 'Light appearance' : 'Dark appearance'}
    >
      {dark ? (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <path d="M21 12.79A9 9 0 1 1 11.21 3 7 7 0 0 0 21 12.79z" />
        </svg>
      ) : (
        <svg width="18" height="18" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round" aria-hidden>
          <circle cx="12" cy="12" r="4" />
          <path d="M12 2v2M12 20v2M4.9 4.9l1.4 1.4M17.7 17.7l1.4 1.4M2 12h2M20 12h2M4.9 19.1l1.4-1.4M17.7 6.3l1.4-1.4" />
        </svg>
      )}
    </motion.button>
  )
}
