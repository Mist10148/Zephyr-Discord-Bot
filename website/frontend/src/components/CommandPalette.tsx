import { Command } from 'cmdk'
import Fuse from 'fuse.js'
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'

type CommandItem = { name: string; aliases: string[]; description: string; category_title: string }

// `open` is lifted so the top bar's trigger pill can open the palette too; the
// ⌘K/Ctrl-K listener stays here, next to the thing it opens.
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange(value: boolean): void }) {
  const [query, setQuery] = useState('')
  const commands = useQuery({ queryKey: ['commands'], queryFn: () => api<{ commands: CommandItem[] }>('/commands') })

  useEffect(() => {
    const listener = (event: KeyboardEvent) => {
      if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); onOpenChange(!open) }
      if (event.key === 'Escape') onOpenChange(false)
    }
    window.addEventListener('keydown', listener)
    return () => window.removeEventListener('keydown', listener)
  }, [open, onOpenChange])

  // Clearing on open rather than on close: the list must not flash the previous
  // query's results in the frame before the input is focused.
  useEffect(() => { if (open) setQuery('') }, [open])

  const filtered = useMemo(() => {
    const items = commands.data?.commands ?? []
    return query ? new Fuse(items, { keys: ['name', 'aliases', 'description'], threshold: .35 }).search(query).map(result => result.item) : items
  }, [commands.data, query])

  if (!open) return null
  return <div className="palette-backdrop" onClick={() => onOpenChange(false)}>
    <Command className="palette" data-glass="1" onClick={event => event.stopPropagation()} shouldFilter={false} label="Command palette">
      <div className="palette-head">
        <span className="lens" aria-hidden />
        <Command.Input value={query} onValueChange={setQuery} placeholder="Search commands…" autoFocus />
        <kbd aria-hidden>esc</kbd>
      </div>
      <Command.List>
        {filtered.map(item => (
          <Command.Item key={item.name} value={item.name} onSelect={() => onOpenChange(false)}>
            <b>{item.name}</b><span>{item.description}</span>
          </Command.Item>
        ))}
        <Command.Empty>No command found.</Command.Empty>
      </Command.List>
    </Command>
  </div>
}
