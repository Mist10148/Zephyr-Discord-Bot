import { useState } from 'react'
import { Link } from 'react-router-dom'
import { useTheme } from '../lib/theme-context'
import { CapsuleToast, GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, SegmentedControl, Skeleton, Slider, Stepper, Toggle, WidgetGrid } from '../components/ios'

// The design-system review page: every primitive on one screen, in whichever theme
// is active, so a visual regression shows up here first. It drives the real theme
// context (not a raw class toggle) so it exercises the same path the app uses.
export function KitchenSink() {
  const { theme, toggle } = useTheme()
  const [segment, setSegment] = useState('Today')
  const [on, setOn] = useState(true)
  const [volume, setVolume] = useState(60)
  const [count, setCount] = useState(2)

  return <main className="app">
    <LargeTitleHeader title="Design system" subtitle={`Every glass primitive, shown in ${theme} appearance.`} />

    <div className="transport">
      <PressableButton onClick={toggle}>Toggle appearance</PressableButton>
      <PressableButton variant="secondary">Secondary</PressableButton>
      <PressableButton variant="danger">Danger</PressableButton>
      <PressableButton disabled>Disabled</PressableButton>
    </div>

    <h2>Segmented control</h2>
    <SegmentedControl values={['Today', 'Tomorrow', 'Week']} value={segment} onChange={setSegment} />

    <h2>Widget cards</h2>
    <WidgetGrid>
      <GlassSurface><h2>Weather</h2><p className="stat-value">26°</p><small className="muted">Partly cloudy</small></GlassSurface>
      <GlassSurface><h2>Air quality</h2><p className="stat-value">Good</p><small className="muted">AQI 24</small></GlassSurface>
    </WidgetGrid>

    <h2>Interactive card</h2>
    <Link to="/kitchen-sink" className="glass glass-interactive feature-card">
      <h2>Hover me</h2>
      <p className="muted">A glass surface with the interactive lift, used for tap targets.</p>
      <span className="feature-go">Open<i className="chevron" aria-hidden /></span>
    </Link>

    <h2>Inset list</h2>
    <ListGroup>
      <ListRow label="Toggle" detail="A pill switch"><Toggle checked={on} onChange={setOn} /></ListRow>
      <ListRow label="Volume" detail={`${volume}%`}><Slider label="Volume" value={volume} onChange={setVolume} /></ListRow>
      <ListRow label="Stepper" detail="Increment / decrement"><Stepper value={count} onChange={setCount} /></ListRow>
      <ListRow label="Navigable row" detail="with a chevron and a leading slot" leading={<span className="guild-icon">ZB</span>} to="/kitchen-sink" />
      <ListRow label="Button row" onClick={() => setOn(value => !value)} />
    </ListGroup>

    <h2>Loading</h2>
    <Skeleton lines={3} />

    <h2>Feedback</h2>
    <CapsuleToast>Saved</CapsuleToast>
    <CapsuleToast tone="error">Something went wrong.</CapsuleToast>

    <p><Link to="/">Back home</Link></p>
  </main>
}
