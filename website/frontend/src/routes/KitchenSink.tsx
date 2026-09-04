import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTheme } from '../lib/theme-context'
import { BackLink, CapsuleToast, Chevron, GlassSurface, IconButton, LargeTitleHeader, ListGroup, ListRow, PressableButton, SectionLabel, SegmentedControl, Skeleton, Slider, Stepper, Toggle, WidgetGrid } from '../components/ios'
import { CloudIcon, DiscIcon, PauseIcon, PlayIcon, RainIcon, ShuffleIcon, SkipIcon, StopIcon, SunIcon } from '../components/icons'
import { useToast } from '../lib/toast'

// The design-system review page: every primitive on one screen, in whichever theme
// is active, so a visual regression shows up here first. It drives the real theme
// context (not a raw class toggle) so it exercises the same path the app uses.
//
// This page is the contract. A primitive that is not rendered here is one nobody
// will notice breaking -- add every new one.
export function KitchenSink() {
  const toast = useToast()
  const { theme, toggle } = useTheme()
  const [segment, setSegment] = useState('Today')
  const [on, setOn] = useState(true)
  const [volume, setVolume] = useState(60)
  const [count, setCount] = useState(2)
  const [checked, setChecked] = useState(true)

  return <main className="app sink">
    <LargeTitleHeader title="Design system" subtitle={`Every glass primitive, shown in ${theme} appearance.`} />

    <SectionLabel>Buttons</SectionLabel>
    <div className="actions">
      <PressableButton onClick={toggle}>Toggle appearance</PressableButton>
      <PressableButton variant="secondary">Secondary</PressableButton>
      <PressableButton variant="danger">Danger</PressableButton>
      <PressableButton disabled>Disabled</PressableButton>
    </div>

    <SectionLabel>Transport</SectionLabel>
    <div className="transport">
      <IconButton variant="primary" size={52} label="Play"><PlayIcon size={18} /></IconButton>
      <IconButton label="Pause"><PauseIcon /></IconButton>
      <IconButton label="Skip"><SkipIcon /></IconButton>
      <IconButton label="Shuffle"><ShuffleIcon /></IconButton>
      <IconButton variant="danger" label="Stop"><StopIcon /></IconButton>
    </div>

    <SectionLabel>Weather glyphs</SectionLabel>
    <div className="actions glyph-row">
      <SunIcon /><CloudIcon /><RainIcon /><DiscIcon />
    </div>
    {/* The art fallback tile, which used to render the words "track art" in 8px
        mono where the thumbnail should be. */}
    <div className="actions"><span className="art-placeholder" aria-hidden><DiscIcon /></span></div>

    <SectionLabel>Segmented control</SectionLabel>
    <SegmentedControl values={['Today', 'Tomorrow', 'Week']} value={segment} onChange={setSegment} />

    <SectionLabel>Widget cards</SectionLabel>
    <WidgetGrid>
      <GlassSurface><h2>Weather</h2><p className="stat-value">26°</p><small className="muted">Partly cloudy</small></GlassSurface>
      <GlassSurface><h2>Air quality</h2><p className="stat-value">Good</p><small className="muted">AQI 24</small></GlassSurface>
    </WidgetGrid>

    <SectionLabel>Glass tiers</SectionLabel>
    <div className="tier-row">
      <GlassSurface tier="thin"><b>Thin</b><p className="muted">Day cards, notices</p></GlassSurface>
      <GlassSurface tier="regular"><b>Regular</b><p className="muted">Content cards, top bar</p></GlassSurface>
      <GlassSurface tier="thick"><b>Thick</b><p className="muted">Sheets, palette, tab bar</p></GlassSurface>
    </div>

    <SectionLabel>Interactive card</SectionLabel>
    <Link to="/kitchen-sink" className="glass glass-regular glass-interactive feature-card" data-glass="1">
      <h2>Hover me</h2>
      <p>A glass surface with the interactive lift, used for tap targets.</p>
      <span className="feature-go">Open<Chevron /></span>
    </Link>

    <SectionLabel>Inset list</SectionLabel>
    <ListGroup>
      <ListRow label="Toggle" detail="A pill switch"><span className="row-actions"><Toggle label="Demo" checked={on} onChange={setOn} /></span></ListRow>
      <ListRow label="Volume"><span className="row-actions"><span className="row-value mono">{volume}%</span><Slider label="Volume" value={volume} onChange={setVolume} /></span></ListRow>
      <ListRow label="Stepper" detail="Increment / decrement"><span className="row-actions"><Stepper value={count} onChange={setCount} /></span></ListRow>
      <ListRow label="Checkbox row" detail="A toggle, not a destination" leading={<span className={`checkbox ${checked ? 'on' : ''}`.trim()} aria-hidden>✓</span>} pressed={checked} onClick={() => setChecked(value => !value)} />
      <ListRow label="Navigable row" detail="with a chevron and a leading slot" leading={<span className="guild-icon mono">ZB</span>} to="/kitchen-sink" className="strong-row" />
      <ListRow label="Value row"><span className="row-value">Asia/Manila</span></ListRow>
    </ListGroup>

    <SectionLabel>Chips and badges</SectionLabel>
    <div className="chip-strip">
      <span className="chip">Feels like 38°</span>
      <span className="chip">Air quality · Good</span>
      <span className="chip warn">Heat index advisory</span>
      <span className="badge accent">Dashboard</span>
      <span className="badge">Discord</span>
    </div>

    {/* Removable chips, used for saved weather places. Two controls per chip:
        the name selects, the × forgets. The active one takes the accent as well
        as aria-current, because a screen-reader-only cue would leave a sighted
        user with no way to tell which item is showing. Deliberately not
        .ios-button -- an action and a saved value reading identically is the
        defect this replaced. */}
    <ul className="place-chips" aria-label="Removable chips">
      <li className="place-chip" data-active>
        <button type="button" className="place-chip-name" aria-current="true">Iloilo City</button>
        <button type="button" className="place-chip-remove" aria-label="Remove Iloilo City">×</button>
      </li>
      <li className="place-chip">
        <button type="button" className="place-chip-name">Cebu</button>
        <button type="button" className="place-chip-remove" aria-label="Remove Cebu">×</button>
      </li>
    </ul>

    <SectionLabel>Status dots</SectionLabel>
    <div className="chip-strip">
      <span className="chip"><i className="dot ok" aria-hidden />Present</span>
      <span className="chip"><i className="dot off" aria-hidden />Absent</span>
      <span className="chip"><i className="dot unknown" aria-hidden />Unknown</span>
    </div>

    <SectionLabel>Fields</SectionLabel>
    <ListGroup>
      <ListRow label="Prefix" detail="1–5 characters">
        <span className="row-actions"><input className="text-input inline w-prefix" aria-label="Prefix" defaultValue="z!" /></span>
      </ListRow>
      <ListRow label="Timezone" detail="e.g. Asia/Manila">
        <span className="row-actions"><input className="text-input inline w-tz invalid" aria-invalid aria-label="Timezone" defaultValue="Manila" /></span>
        <p className="field-error" role="alert"><i className="toast-badge" aria-hidden>!</i>Not an IANA name — use Region/City.</p>
      </ListRow>
    </ListGroup>

    <SectionLabel>Loading</SectionLabel>
    <Skeleton lines={3} />

    <SectionLabel>Feedback</SectionLabel>
    <div className="stack">
      <CapsuleToast tone="success">Saved</CapsuleToast>
      <CapsuleToast>Paused subscriptions keep their settings.</CapsuleToast>
      <CapsuleToast tone="error">Something went wrong.</CapsuleToast>
      <CapsuleToast tone="success" action={{ label: 'Undo', onClick: () => undefined }}>Removed a track</CapsuleToast>
      <div className="error-banner" role="alert"><i aria-hidden>!</i><span>You cancelled the Discord sign-in.</span></div>
    </div>
    {/* The host itself, not just the visual. Rendered as buttons because the
        thing worth reviewing is the stack: three at most, neutral and success
        self-dismissing, errors staying until dismissed, and the region fixed so
        none of it moves the page. */}
    <p className="muted small-note">Push a few to see the region stack them, top-right on desktop and above the tab bar on a phone.</p>
    <div className="chip-strip">
      <PressableButton className="small" onClick={() => toast.info('Working on it.')}>Neutral toast</PressableButton>
      <PressableButton className="small" onClick={() => toast.success('Queued Bohemian Rhapsody')}>Success toast</PressableButton>
      <PressableButton className="small" variant="danger" onClick={() => toast.error('Nothing is playing.')}>Error toast</PressableButton>
      <PressableButton className="small" variant="secondary" onClick={() => toast.success('Removed a track', { label: 'Undo', onClick: () => toast.info('Put it back') })}>With an action</PressableButton>
    </div>

    <BackLink to="/">Back home</BackLink>
  </main>
}
