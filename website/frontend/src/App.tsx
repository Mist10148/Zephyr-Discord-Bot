import { useState } from 'react'
import { Route, Routes, useLocation } from 'react-router-dom'
import { AppShell } from './components/AppShell'
import { CommandPalette } from './components/CommandPalette'
import { RequireAuth } from './components/RequireAuth'
import { TabBar } from './components/TabBar'
import { GuildAudit } from './routes/GuildAudit'
import { GuildMusic } from './routes/GuildMusic'
import { GuildOverview } from './routes/GuildOverview'
import { GuildSettings } from './routes/GuildSettings'
import { GuildWeatherAlerts } from './routes/GuildWeatherAlerts'
import { GuildAI } from './routes/GuildAI'
import { Guilds } from './routes/Guilds'
import { Home } from './routes/Home'
import { KitchenSink } from './routes/KitchenSink'
import { Login } from './routes/Login'
import { NotFound } from './routes/NotFound'
import { Weather } from './routes/Weather'

export default function App() {
  const { pathname } = useLocation()
  // Palette state lives here so the top bar's trigger and the ⌘K listener drive the
  // same thing. It is not mounted on the signed-out screen, where it would only
  // fetch /commands for nothing.
  const [paletteOpen, setPaletteOpen] = useState(false)
  const showPalette = pathname !== '/login'

  return <AppShell onOpenPalette={() => setPaletteOpen(true)}>
    <Routes>
      <Route path="/" element={<Home />} />
      <Route path="/weather" element={<Weather />} />
      <Route path="/kitchen-sink" element={<KitchenSink />} />
      <Route path="/login" element={<Login />} />
      <Route path="/g" element={<RequireAuth><Guilds /></RequireAuth>} />
      <Route path="/g/:guildId" element={<RequireAuth><GuildOverview /></RequireAuth>} />
      <Route path="/g/:guildId/music" element={<RequireAuth><GuildMusic /></RequireAuth>} />
      <Route path="/g/:guildId/weather-alerts" element={<RequireAuth><GuildWeatherAlerts /></RequireAuth>} />
      <Route path="/g/:guildId/ai" element={<RequireAuth><GuildAI /></RequireAuth>} />
      <Route path="/g/:guildId/settings" element={<RequireAuth><GuildSettings /></RequireAuth>} />
      <Route path="/g/:guildId/audit" element={<RequireAuth><GuildAudit /></RequireAuth>} />
      <Route path="*" element={<NotFound />} />
    </Routes>
    {showPalette && <CommandPalette open={paletteOpen} onOpenChange={setPaletteOpen} />}
    <TabBar />
  </AppShell>
}
