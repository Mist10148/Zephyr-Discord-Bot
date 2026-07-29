import { Command } from 'cmdk'
import Fuse from 'fuse.js'
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

type CommandItem = { name: string; aliases: string[]; description: string; category_title: string }
export function CommandPalette() {
  const [open, setOpen] = useState(false); const [query, setQuery] = useState('')
  const commands = useQuery({ queryKey:['commands'], queryFn: () => api<{commands: CommandItem[]}>('/commands') })
  useEffect(() => { const listener = (event: KeyboardEvent) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); setOpen(value => !value) } }; window.addEventListener('keydown', listener); return () => window.removeEventListener('keydown', listener) }, [])
  const filtered = useMemo(() => { const items = commands.data?.commands ?? []; return query ? new Fuse(items, { keys: ['name', 'aliases', 'description'], threshold: .35 }).search(query).map(result => result.item) : items }, [commands.data, query])
  if (!open) return null
  return <div className="palette-backdrop" onClick={() => setOpen(false)}><Command className="palette" onClick={event => event.stopPropagation()} shouldFilter={false}><Command.Input value={query} onValueChange={setQuery} placeholder="Search commands…" /><Command.List>{filtered.map(item => <Command.Item key={item.name} value={item.name} onSelect={() => setOpen(false)}><b>{item.name}</b><span>{item.description}</span></Command.Item>)}<Command.Empty>No command found.</Command.Empty></Command.List></Command></div>
}
