import { motion } from 'motion/react'
import { useTheme } from '../lib/theme-context'
import { haptic } from '../lib/haptics'
import { MoonIcon, SunSmallIcon } from './icons'

// A single control that reads its own state from the theme context. The glyphs are
// inline SVG so they inherit currentColor and need no icon dependency.
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
      {dark ? <MoonIcon /> : <SunSmallIcon />}
    </motion.button>
  )
}
