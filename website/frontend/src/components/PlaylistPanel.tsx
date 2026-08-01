import { useEffect, useState } from 'react'
import { useMutation, useQueryClient } from '@tanstack/react-query'
import { DndContext, KeyboardSensor, PointerSensor, closestCenter, useSensor, useSensors } from '@dnd-kit/core'
import type { DragEndEvent } from '@dnd-kit/core'
import { SortableContext, arrayMove, sortableKeyboardCoordinates, useSortable, verticalListSortingStrategy } from '@dnd-kit/sortable'
import { CSS } from '@dnd-kit/utilities'
import { api } from '../lib/api'
import { haptic } from '../lib/haptics'
import { formatDuration, usePlaylist, usePlaylists } from '../lib/player'
import { ErrorNote } from './ErrorNote'
import { GlassSurface, IconButton, ListGroup, ListRow, PressableButton, Sheet, Skeleton } from './ios'
import type { PlaylistTrack } from '../types/api'

function SortableTrack({ id, track, onRemove }: { id: string; track: PlaylistTrack; onRemove(): void }) {
  const { attributes, listeners, setNodeRef, transform, transition, isDragging } = useSortable({ id })
  return <div
    ref={setNodeRef}
    className={`list-row sortable${isDragging ? ' dragging' : ''}`}
    style={{ transform: CSS.Transform.toString(transform), transition }}
  >
    {/* The drag handle carries dnd-kit's keyboard sensor bindings, so the list is
        reorderable with the keyboard as well as by pointer. Drag-and-drop with no
        keyboard path is the accessibility failure this component would otherwise be. */}
    <button className="drag-handle" aria-label={`Reorder ${track.title}`} {...attributes} {...listeners}>⠿</button>
    <span className="row-label">{track.title}<small>{track.url ? formatDuration(track.duration_s) : 'Resolved when it plays'}</small></span>
    <span className="row-actions"><PressableButton className="small" variant="danger" onClick={onRemove}>Remove</PressableButton></span>
  </div>
}

function PlaylistEditor({ playlistId, onClose }: { playlistId: number; onClose(): void }) {
  const client = useQueryClient()
  const playlist = usePlaylist(playlistId)
  const [tracks, setTracks] = useState<PlaylistTrack[] | null>(null)
  const sensors = useSensors(useSensor(PointerSensor), useSensor(KeyboardSensor, { coordinateGetter: sortableKeyboardCoordinates }))

  // Editing is local until saved, so a drag is not undone by a refetch mid-gesture.
  useEffect(() => { setTracks(playlist.data ? playlist.data.tracks : null) }, [playlist.data])

  const save = useMutation({
    mutationFn: (next: PlaylistTrack[]) => api(`/playlists/${playlistId}`, { method: 'PATCH', body: { tracks: next } }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['playlist', playlistId] }); client.invalidateQueries({ queryKey: ['playlists'] }) },
  })

  if (playlist.isPending || tracks === null) return <Skeleton lines={5} />
  if (playlist.error) return <ErrorNote error={playlist.error} onRetry={() => playlist.refetch()} />

  const key = (track: PlaylistTrack, index: number) => `${index}-${track.url ?? track.title}`
  const onDragEnd = (event: DragEndEvent) => {
    const { active, over } = event
    if (!over || active.id === over.id) return
    const from = tracks.findIndex((track, index) => key(track, index) === active.id)
    const to = tracks.findIndex((track, index) => key(track, index) === over.id)
    if (from < 0 || to < 0) return
    haptic(8)
    setTracks(arrayMove(tracks, from, to))
  }

  const dirty = JSON.stringify(tracks) !== JSON.stringify(playlist.data!.tracks)
  return <>
    <h2>Edit — {playlist.data!.name}</h2>
    <p>Drag to reorder, or use the handle with arrow keys.</p>
    <DndContext sensors={sensors} collisionDetection={closestCenter} onDragEnd={onDragEnd}>
      <SortableContext items={tracks.map(key)} strategy={verticalListSortingStrategy}>
        <ListGroup>
          {tracks.map((track, index) => <SortableTrack
            key={key(track, index)}
            id={key(track, index)}
            track={track}
            onRemove={() => setTracks(tracks.filter((_, position) => position !== index))}
          />)}
        </ListGroup>
      </SortableContext>
    </DndContext>
    {tracks.length === 0 && <p className="muted">This playlist is now empty. Saving it will leave it empty.</p>}
    {save.error && <ErrorNote error={save.error} onRetry={() => save.reset()} />}
    <div className="sheet-actions">
      <PressableButton variant="secondary" onClick={onClose}>Close</PressableButton>
      <PressableButton disabled={!dirty || save.isPending} onClick={() => save.mutate(tracks)}>{save.isPending ? 'Saving…' : 'Save order'}</PressableButton>
    </div>
  </>
}

function ImportForm({ guildId, onDone }: { guildId: string | undefined; onDone(): void }) {
  const client = useQueryClient()
  const [url, setUrl] = useState('')
  const importer = useMutation({
    mutationFn: () => api('/playlists/import/spotify', { method: 'POST', body: { url, guild_id: guildId } }),
    onSuccess: () => { client.invalidateQueries({ queryKey: ['playlists'] }); setUrl(''); onDone() },
  })
  return <>
    <h2>Import from Spotify</h2>
    <p>Zephyr copies titles only — each track is matched to audio the first time it plays, so a link that stops working later does not take the playlist with it.</p>
    <label className="field">
      <span>Playlist link</span>
      <input className="text-input full" value={url} placeholder="https://open.spotify.com/playlist/…" onChange={event => setUrl(event.target.value)} />
    </label>
    {importer.error && <ErrorNote error={importer.error} onRetry={() => importer.reset()} />}
    <div className="sheet-actions">
      <PressableButton variant="secondary" onClick={onDone}>Cancel</PressableButton>
      <PressableButton disabled={!url.trim() || importer.isPending} onClick={() => importer.mutate()}>{importer.isPending ? 'Importing…' : 'Import'}</PressableButton>
    </div>
  </>
}

export function PlaylistPanel({ guildId }: { guildId: string | undefined }) {
  const client = useQueryClient()
  const playlists = usePlaylists(guildId)
  const [editing, setEditing] = useState<number | null>(null)
  const [importing, setImporting] = useState(false)

  const load = useMutation({
    mutationFn: (playlistId: number) => api(`/playlists/${playlistId}/load`, { method: 'POST', body: { guild_id: guildId } }),
    onSettled: () => client.invalidateQueries({ queryKey: ['player', guildId] }),
  })
  const remove = useMutation({
    mutationFn: (playlistId: number) => api(`/playlists/${playlistId}`, { method: 'DELETE' }),
    onSuccess: () => client.invalidateQueries({ queryKey: ['playlists'] }),
  })

  return <section>
    <div className="section-head">
      <h2>Playlists</h2>
      <PressableButton variant="secondary" className="small" onClick={() => setImporting(true)}>Import from Spotify</PressableButton>
    </div>
    {playlists.isPending && <Skeleton lines={3} />}
    {playlists.error && <ErrorNote error={playlists.error} onRetry={() => playlists.refetch()} />}
    {playlists.data && (playlists.data.playlists.length === 0
      ? <GlassSurface tier="thin"><p className="muted">No playlists yet. Queue something up in Discord and run <code>/save</code>, or import one from Spotify.</p></GlassSurface>
      : <ListGroup>
        {playlists.data.playlists.map(playlist => <ListRow
          key={playlist.id}
          label={playlist.mine ? playlist.name : `${playlist.name} (shared)`}
          detail={`${playlist.track_count} track${playlist.track_count === 1 ? '' : 's'} · ${formatDuration(playlist.duration_s)}`}
        >
          <span className="row-actions">
            <PressableButton className="small soft" onClick={() => { haptic(8); load.mutate(playlist.id) }}>Queue</PressableButton>
            {playlist.mine && <PressableButton variant="secondary" className="small" onClick={() => setEditing(playlist.id)}>Edit</PressableButton>}
            {playlist.mine && <IconButton variant="danger" size={30} label={`Delete ${playlist.name}`} onClick={() => { haptic(15); remove.mutate(playlist.id) }}>×</IconButton>}
          </span>
        </ListRow>)}
      </ListGroup>)}

    {load.error && <ErrorNote error={load.error} onRetry={() => load.reset()} />}
    {remove.error && <ErrorNote error={remove.error} onRetry={() => remove.reset()} />}

    <Sheet open={editing !== null} onOpenChange={open => !open && setEditing(null)} label="Edit playlist">
      {editing !== null && <PlaylistEditor playlistId={editing} onClose={() => setEditing(null)} />}
    </Sheet>
    <Sheet open={importing} onOpenChange={setImporting} label="Import from Spotify">
      <ImportForm guildId={guildId} onDone={() => setImporting(false)} />
    </Sheet>
  </section>
}
