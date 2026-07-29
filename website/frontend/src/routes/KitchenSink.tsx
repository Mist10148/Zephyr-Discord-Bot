import { useState } from 'react'
import { Link } from 'react-router-dom'
import { GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, SegmentedControl, Toggle, WidgetGrid } from '../components/ios'

export function KitchenSink() { const [selected, setSelected] = useState('Today'); const [enabled, setEnabled] = useState(true); return <main className="app"><LargeTitleHeader title="Kitchen Sink" /><SegmentedControl values={['Today','Tomorrow']} value={selected} onChange={setSelected} /><WidgetGrid><GlassSurface><h2>Weather</h2><p>26° • Partly cloudy</p></GlassSurface><GlassSurface><h2>Air quality</h2><p>Good</p></GlassSurface></WidgetGrid><ListGroup><ListRow label="Dark appearance"><Toggle checked={enabled} onChange={setEnabled} /></ListRow></ListGroup><p><PressableButton onClick={() => document.documentElement.classList.toggle('dark')}>Toggle theme</PressableButton></p><Link to="/">Back</Link></main> }
