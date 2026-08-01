import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import { QueryClientProvider } from '@tanstack/react-query'
import { BrowserRouter } from 'react-router-dom'
import App from './App'
import { queryClient } from './lib/query'
import { ThemeProvider } from './lib/theme'
// Self-hosted, not the Google Fonts CDN: website/security.py sets `font-src 'self'
// data:`, so a third-party font request would be blocked by CSP in production —
// and a bundled font keeps the display face working offline in the PWA.
import '@fontsource-variable/source-serif-4'
import './styles/theme.css'

createRoot(document.getElementById('root')!).render(<StrictMode><ThemeProvider><QueryClientProvider client={queryClient}><BrowserRouter><App /></BrowserRouter></QueryClientProvider></ThemeProvider></StrictMode>)
