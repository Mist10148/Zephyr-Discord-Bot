import Fuse from 'fuse.js'
import { useMemo, useState } from 'react'
import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { GlassSurface, LargeTitleHeader, ListGroup, ListRow, PressableButton, SectionLabel, Skeleton } from '../components/ios'
import { useToast } from '../lib/toast'

type CommandItem = { name: string; aliases: string[]; args: { name: string; required: boolean }[]; description: string; category: string; category_title: string; emoji: string }
type CommandResponse = { commands: CommandItem[]; categories: { key: string; title: string; emoji: string }[] }

/** `#commands-<key>`, so a category is linkable as well as scrollable-to. */
const sectionId = (key: string) => `commands-${key}`

function CommandRow({ command, onCopy }: { command: CommandItem; onCopy(): void }) {
  return <ListRow
    label={<><code>/{command.name}</code>{command.args.map(arg => <span key={arg.name} className="command-arg"> {arg.required ? `<${arg.name}>` : `[${arg.name}]`}</span>)}</>}
    // Aliases have always been a Fuse search key and were never rendered, so
    // searching one returned a hit with no visible reason for the match.
    detail={<>{command.description}{command.aliases.length > 0 && <span className="command-aliases"> · also {command.aliases.map(alias => `/${alias}`).join(', ')}</span>}</>}
  >
    <span className="row-actions">
      <PressableButton className="small soft" variant="secondary" label={`Copy /${command.name}`} onClick={onCopy}>Copy</PressableButton>
    </span>
  </ListRow>
}

export function Commands() {
  const [search, setSearch] = useState('')
  const toast = useToast()
  const query = useQuery({ queryKey: ['commands'], queryFn: () => api<CommandResponse>('/commands') })

  // `?? []` produces a new array identity every render, which would make the
  // memo below re-run each time and rebuild the Fuse index for nothing.
  const all = useMemo(() => query.data?.commands ?? [], [query.data])
  const commands = useMemo(
    () => search ? new Fuse(all, { keys: ['name', 'aliases', 'description'], threshold: .35 }).search(search).map(item => item.item) : all,
    [all, search],
  )

  const copy = (command: CommandItem) => {
    // Mirrors what the palette already does with a selected command.
    navigator.clipboard?.writeText(`/${command.name}`)
      .then(() => toast.success(`Copied /${command.name}`))
      .catch(() => toast.error('Could not copy to the clipboard.'))
  }

  // Categories that have something to show under the current filter. The chip
  // row tracks the filter rather than the full set, so a chip never scrolls to
  // a section that is not there.
  const visible = (query.data?.categories ?? []).filter(category => commands.some(command => command.category === category.key))

  return <main className="app"><LargeTitleHeader title="Commands" subtitle="Every Zephyr command, grouped so you can find the right one quickly." />
    <div className="search-field"><span className="lens" aria-hidden /><input className="search-input" aria-label="Search commands" value={search} onChange={event => setSearch(event.target.value)} placeholder="Search commands…" /></div>

    {/* 73 rows in one scroll with no jump list and no result count. The chip row
        is sticky so it stays reachable however far down the page you are. */}
    {visible.length > 1 && (
      <nav className="category-chips" aria-label="Jump to a category">
        {visible.map(category => (
          <a key={category.key} className="chip category-chip" href={`#${sectionId(category.key)}`}>
            <span aria-hidden>{category.emoji}</span>{category.title}
          </a>
        ))}
      </nav>
    )}

    {query.data && (
      <p className="muted small-note" role="status">
        {search ? `${commands.length} of ${all.length} commands` : `${all.length} commands`}
      </p>
    )}

    {query.isPending && <Skeleton variant="rows" count={8} />}
    {query.error && <GlassSurface><p>Commands are unavailable right now.</p></GlassSurface>}

    {visible.map(category => {
      const items = commands.filter(command => command.category === category.key)
      return <section key={category.key} id={sectionId(category.key)} className="command-section">
        <SectionLabel>{category.emoji} {category.title}</SectionLabel>
        <ListGroup>{items.map(command => <CommandRow key={command.name} command={command} onCopy={() => copy(command)} />)}</ListGroup>
      </section>
    })}

    {query.data && commands.length === 0 && <GlassSurface tier="thin"><p className="muted">No command matches “{search}”.</p></GlassSurface>}
  </main>
}
