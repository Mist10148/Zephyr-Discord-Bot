import { Command } from 'cmdk'
import Fuse from 'fuse.js'
import { useEffect, useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { useNavigate } from 'react-router-dom'
import { api } from '../lib/api'
import { useMe } from '../lib/auth'
import { useTheme } from '../lib/theme-context'

type CommandItem = { name: string; aliases: string[]; description: string; category: string; category_title: string }
export function CommandPalette({ open, onOpenChange }: { open: boolean; onOpenChange(value: boolean): void }) {
  const [query, setQuery] = useState(''); const [notice, setNotice] = useState(''); const navigate = useNavigate(); const me = useMe(); const { patchPreferences, preferences } = useTheme()
  const commands = useQuery({ queryKey: ['commands'], queryFn: () => api<{ commands: CommandItem[] }>('/commands') })
  useEffect(() => { const listener = (event: KeyboardEvent) => { if ((event.metaKey || event.ctrlKey) && event.key.toLowerCase() === 'k') { event.preventDefault(); onOpenChange(!open) } if (event.key === 'Escape') onOpenChange(false) }; window.addEventListener('keydown', listener); return () => window.removeEventListener('keydown', listener) }, [open, onOpenChange])
  useEffect(() => { if (open) { setQuery(''); setNotice('') } }, [open])
  const filtered = useMemo(() => { const items = commands.data?.commands ?? []; return query ? new Fuse(items, { keys: ['name', 'aliases', 'description'], threshold: .35 }).search(query).map(result => result.item) : items }, [commands.data, query])
  const go = (path: string) => { navigate(path); onOpenChange(false) }
  const copy = async (item: CommandItem) => { try { await navigator.clipboard.writeText(`/${item.name}`); setNotice(`Copied /${item.name}`) } catch { setNotice(`Use /${item.name} in Discord`) } }
  if (!open) return null
  return <div className="palette-backdrop" onClick={() => onOpenChange(false)}><Command className="palette" data-glass="1" onClick={event => event.stopPropagation()} shouldFilter={false} label="Command palette"><div className="palette-head"><span className="lens" aria-hidden /><Command.Input value={query} onValueChange={setQuery} placeholder="Search pages and commands…" autoFocus /><kbd aria-hidden>esc</kbd></div><Command.List>
    {!query && <Command.Group heading="Navigate"><Command.Item value="home" onSelect={() => go('/')}>Home</Command.Item><Command.Item value="weather" onSelect={() => go('/weather')}>Weather</Command.Item><Command.Item value="commands" onSelect={() => go('/commands')}>Command reference</Command.Item><Command.Item value="settings" onSelect={() => go('/settings')}>Website settings</Command.Item><Command.Item value="theme" onSelect={() => patchPreferences({ theme: preferences.theme === 'dark' ? 'light' : 'dark' })}>Toggle appearance</Command.Item>{me.data?.guilds.map(guild => <Command.Item key={guild.id} value={`server ${guild.name}`} onSelect={() => go(`/g/${guild.id}`)}>Server · {guild.name}</Command.Item>)}</Command.Group>}
    <Command.Group heading="Discord commands">{filtered.map(item => <Command.Item key={item.name} value={item.name} onSelect={() => copy(item)}><b>/{item.name}</b><span>{item.description}</span></Command.Item>)}</Command.Group><Command.Empty>No match found.</Command.Empty>
  </Command.List>{notice && <p className="palette-notice" role="status">{notice}</p>}</Command></div>
}
