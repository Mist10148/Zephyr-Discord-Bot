import Fuse from 'fuse.js'
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { BackLink, GlassSurface, LargeTitleHeader, ListGroup, ListRow, SectionLabel, Skeleton } from '../components/ios'

type CommandItem = { name: string; aliases: string[]; args: { name: string; required: boolean }[]; description: string; category: string; category_title: string; emoji: string }
type CommandResponse = { commands: CommandItem[]; categories: { key: string; title: string; emoji: string }[] }
export function Commands() {
  const [search, setSearch] = useState(''); const query = useQuery({ queryKey: ['commands'], queryFn: () => api<CommandResponse>('/commands') })
  const commands = useMemo(() => { const all = query.data?.commands ?? []; return search ? new Fuse(all, { keys: ['name', 'aliases', 'description'], threshold: .35 }).search(search).map(item => item.item) : all }, [query.data, search])
  return <main className="app"><LargeTitleHeader title="Commands" subtitle="Every Zephyr command, grouped so you can find the right one quickly." />
    <div className="search-field"><span className="lens" aria-hidden /><input className="search-input" aria-label="Search commands" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search commands…" /></div>
    {query.isPending && <Skeleton lines={6} />}{query.error && <GlassSurface><p>Commands are unavailable right now.</p></GlassSurface>}
    {query.data?.categories.map(category => { const items = commands.filter(command => command.category === category.key); return items.length ? <section key={category.key}><SectionLabel>{category.emoji} {category.title}</SectionLabel><ListGroup>{items.map(command => <ListRow key={command.name} label={<><code>/{command.name}</code>{command.args.map(arg => <span key={arg.name} className="command-arg"> {arg.required ? `<${arg.name}>` : `[${arg.name}]`}</span>)}</>} detail={command.description} />)}</ListGroup></section> : null })}
    {query.data && commands.length === 0 && <GlassSurface tier="thin"><p className="muted">No command matches “{search}”.</p></GlassSurface>}<BackLink to="/">Back home</BackLink>
  </main>
}
