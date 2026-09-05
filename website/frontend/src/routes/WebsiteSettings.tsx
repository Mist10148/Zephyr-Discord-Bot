import { BackLink, GlassSurface, LargeTitleHeader, ListGroup, ListRow, SectionLabel, SegmentedControl } from '../components/ios'
import { useTheme } from '../lib/theme-context'

const labels: Record<string, string> = { warm: 'Warm Glass', twilight: 'Twilight', forest: 'Forest', comfortable: 'Comfortable', compact: 'Compact', system: 'System', reduced: 'Reduced', metric: 'Metric', imperial: 'Imperial', cards: 'Cards', list: 'List', all: 'All', pinned: 'Pinned', installed: 'Installed', 'needs-bot': 'Needs Zephyr', unknown: 'Unknown', 'name-asc': 'Name A–Z', 'name-desc': 'Name Z–A', recent: 'Recently opened', status: 'Bot status', light: 'Light', dark: 'Dark', '90': '90%', '100': '100%', '110': '110%' }
function Choice({ label, value, values, setting }: { label: string; value: string; values: string[]; setting: 'theme' | 'palette' | 'density' | 'textScale' | 'motion' | 'units' | 'dashboardView' | 'guildCategory' | 'guildSort' }) {
  const { patchPreferences } = useTheme()
  return <ListRow label={label}><SegmentedControl value={value} values={values} labels={labels} onChange={next => patchPreferences({ [setting]: next } as never)} /></ListRow>
}
export function WebsiteSettings() {
  const { preferences } = useTheme()
  return <main className="app"><LargeTitleHeader title="Website settings" subtitle="Make Zephyr feel right on this browser." />
    <SectionLabel>Appearance</SectionLabel><ListGroup>
      <Choice label="Theme" setting="theme" value={preferences.theme} values={['light', 'system', 'dark']} />
      <Choice label="Palette" setting="palette" value={preferences.palette} values={['warm', 'twilight', 'forest']} />
      <Choice label="Density" setting="density" value={preferences.density} values={['comfortable', 'compact']} />
      <Choice label="Text size" setting="textScale" value={preferences.textScale} values={['90', '100', '110']} />
      <Choice label="Motion" setting="motion" value={preferences.motion} values={['system', 'reduced']} />
    </ListGroup>
    <SectionLabel>Defaults</SectionLabel><ListGroup>
      <Choice label="Weather units" setting="units" value={preferences.units} values={['metric', 'imperial']} />
      <Choice label="Server view" setting="dashboardView" value={preferences.dashboardView} values={['cards', 'list']} />
      <Choice label="Server category" setting="guildCategory" value={preferences.guildCategory} values={['all', 'pinned', 'installed', 'needs-bot', 'unknown']} />
      <Choice label="Server sort" setting="guildSort" value={preferences.guildSort} values={['pinned', 'recent', 'name-asc', 'name-desc', 'status']} />
    </ListGroup>
    <GlassSurface tier="thin" className="notice"><p>These preferences stay in this browser. Discord account data and private dashboard results are never saved here.</p></GlassSurface>
    <BackLink to="/">Back home</BackLink>
  </main>
}
