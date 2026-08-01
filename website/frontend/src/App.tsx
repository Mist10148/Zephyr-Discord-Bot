import { lazy, Suspense, useState } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { CommandPalette } from './components/CommandPalette'
import { RequireAuth } from './components/RequireAuth'
import { RouteErrorBoundary } from './components/RouteErrorBoundary'
import { TabBar } from './components/TabBar'
import { PwaUpdate } from './components/PwaUpdate'
import { MiniPlayer } from './components/MiniPlayer'
import { Skeleton } from './components/ios'

const Home = lazy(() => import('./routes/Home').then(m => ({ default: m.Home })))
const Weather = lazy(() => import('./routes/Weather').then(m => ({ default: m.Weather })))
const KitchenSink = lazy(() => import('./routes/KitchenSink').then(m => ({ default: m.KitchenSink })))
const Login = lazy(() => import('./routes/Login').then(m => ({ default: m.Login })))
const Guilds = lazy(() => import('./routes/Guilds').then(m => ({ default: m.Guilds })))
const GuildOverview = lazy(() => import('./routes/GuildOverview').then(m => ({ default: m.GuildOverview })))
const GuildMusic = lazy(() => import('./routes/GuildMusic').then(m => ({ default: m.GuildMusic })))
const GuildWeatherAlerts = lazy(() => import('./routes/GuildWeatherAlerts').then(m => ({ default: m.GuildWeatherAlerts })))
const GuildAI = lazy(() => import('./routes/GuildAI').then(m => ({ default: m.GuildAI })))
const GuildSettings = lazy(() => import('./routes/GuildSettings').then(m => ({ default: m.GuildSettings })))
const GuildAudit = lazy(() => import('./routes/GuildAudit').then(m => ({ default: m.GuildAudit })))
const WebsiteSettings = lazy(() => import('./routes/WebsiteSettings').then(m => ({ default: m.WebsiteSettings })))
const Commands = lazy(() => import('./routes/Commands').then(m => ({ default: m.Commands })))
const NotFound = lazy(() => import('./routes/NotFound').then(m => ({ default: m.NotFound })))
const loading = <main className="app"><Skeleton lines={6} /></main>

export default function App() {
  const { pathname } = useLocation(); const [paletteOpen, setPaletteOpen] = useState(false); const showPalette = pathname !== '/login'
  return <AppShell onOpenPalette={() => setPaletteOpen(true)}><a className="skip-link" href="#main-content">Skip to content</a><RouteErrorBoundary><Suspense fallback={loading}><Routes>
    <Route path="/" element={<Home />} /><Route path="/weather" element={<Weather />} /><Route path="/commands" element={<Commands />} /><Route path="/settings" element={<WebsiteSettings />} /><Route path="/kitchen-sink" element={<KitchenSink />} /><Route path="/login" element={<Login />} />
    <Route path="/g" element={<RequireAuth><Guilds /></RequireAuth>} /><Route path="/g/:guildId" element={<RequireAuth><GuildOverview /></RequireAuth>} /><Route path="/g/:guildId/music" element={<RequireAuth><GuildMusic /></RequireAuth>} /><Route path="/g/:guildId/weather-alerts" element={<RequireAuth><GuildWeatherAlerts /></RequireAuth>} /><Route path="/g/:guildId/ai" element={<RequireAuth><GuildAI /></RequireAuth>} /><Route path="/g/:guildId/settings" element={<RequireAuth><GuildSettings /></RequireAuth>} /><Route path="/g/:guildId/audit" element={<RequireAuth><GuildAudit /></RequireAuth>} /><Route path="*" element={<NotFound />} />
  </Routes></Suspense></RouteErrorBoundary><MiniPlayer />{showPalette && <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />}<PwaUpdate /><TabBar /></AppShell>
}
