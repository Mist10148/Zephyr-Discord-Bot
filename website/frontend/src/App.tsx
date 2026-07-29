import { Route, Routes } from 'react-router-dom'
import { CommandPalette } from './components/CommandPalette'
import { Home } from './routes/Home'
import { KitchenSink } from './routes/KitchenSink'
import { NotFound } from './routes/NotFound'
import { Weather } from './routes/Weather'

export default function App() {
  return <><Routes><Route path="/" element={<Home />} /><Route path="/weather" element={<Weather />} /><Route path="/kitchen-sink" element={<KitchenSink />} /><Route path="*" element={<NotFound />} /></Routes><CommandPalette /></>
}
