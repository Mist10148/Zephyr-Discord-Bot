/* Runs before the module bundle. Keep its validation in sync with lib/preferences.ts. */
(() => { try {
  const defaults = { theme: 'system', palette: 'warm', density: 'comfortable', textScale: '100', motion: 'system' }
  const raw = JSON.parse(localStorage.getItem('zephyr-preferences-v2') || '{}')
  const oldTheme = localStorage.getItem('zephyr-theme')
  const value = { ...defaults, ...raw }
  if (!['light', 'system', 'dark'].includes(value.theme)) value.theme = oldTheme === 'light' || oldTheme === 'dark' ? oldTheme : 'system'
  if (!['warm', 'twilight', 'forest'].includes(value.palette)) value.palette = 'warm'
  if (!['comfortable', 'compact'].includes(value.density)) value.density = 'comfortable'
  if (!['90', '100', '110'].includes(value.textScale)) value.textScale = '100'
  if (!['system', 'reduced'].includes(value.motion)) value.motion = 'system'
  const dark = value.theme === 'dark' || (value.theme === 'system' && matchMedia('(prefers-color-scheme: dark)').matches)
  document.documentElement.classList.toggle('dark', dark); document.documentElement.style.colorScheme = dark ? 'dark' : 'light'
  document.documentElement.dataset.palette = value.palette; document.documentElement.dataset.density = value.density; document.documentElement.dataset.textScale = value.textScale; document.documentElement.dataset.motion = value.motion
} catch (_) {} })()
