import { useEffect, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { api } from './api'
import type { GuildMeta, Player, PlaylistDetail, PlaylistSummary } from '../types/api'

// A .ts file, not .tsx: eslint-plugin-react-refresh only scans .jsx/.tsx, so hooks
// living beside plain helpers here cannot trip react-refresh/only-export-components,
// which CI treats as an error via --max-warnings=0. Same reasoning as lib/auth.ts.

// The one place polling happens. The plan puts polling on the player snapshot and
// nowhere else, and three seconds is what the bot's own snapshot loop is tuned to --
// polling faster would only re-read the same value.
export const PLAYER_POLL_MS = 3000

export function usePlayer(guildId: string | undefined) {
  return useQuery({
    queryKey: ['player', guildId],
    queryFn: () => api<Player>(`/guilds/${guildId}/player`),
    enabled: !!guildId,
    refetchInterval: PLAYER_POLL_MS,
    // The snapshot is the definition of current; a cached one is never worth
    // showing in preference to a fetch.
    staleTime: 0,
  })
}

export function usePlayerAction(guildId: string | undefined) {
  const client = useQueryClient()
  return useMutation({
    mutationFn: ({ action, args }: { action: string; args?: Record<string, unknown> }) =>
      api<Record<string, unknown>>(`/guilds/${guildId}/player/${action}`, { method: 'POST', body: args ?? {} }),
    // The bot republishes its snapshot before answering, so by the time this
    // resolves a refetch reads the result of the press rather than the state
    // before it -- no optimistic update needed, and none that can be wrong.
    onMutate: async ({ action, args }) => {
      if (action !== 'autoplay') return undefined
      await client.cancelQueries({ queryKey: ['player', guildId] })
      const previous = client.getQueryData<Player>(['player', guildId])
      if (previous && typeof args?.enabled === 'boolean') client.setQueryData<Player>(['player', guildId], { ...previous, autoplay: args.enabled })
      return { previous }
    },
    onError: (_error, _vars, context) => { if (context?.previous) client.setQueryData(['player', guildId], context.previous) },
    onSettled: () => client.invalidateQueries({ queryKey: ['player', guildId] }),
  })
}

export function useGuildMeta(guildId: string | undefined) {
  return useQuery({
    queryKey: ['guild-meta', guildId],
    queryFn: () => api<GuildMeta>(`/guilds/${guildId}/meta`),
    enabled: !!guildId,
    // Every read is a round trip to the bot, so this is cached hard. Channels
    // and roles change rarely, and a stale name is a cosmetic problem where a
    // request storm would be a real one.
    staleTime: 5 * 60_000,
    retry: false,
  })
}

export function usePlaylists(guildId: string | undefined) {
  return useQuery({
    queryKey: ['playlists', guildId],
    queryFn: () => api<{ playlists: PlaylistSummary[] }>(`/playlists${guildId ? `?guild_id=${guildId}` : ''}`),
  })
}

export function usePlaylist(playlistId: number | null) {
  return useQuery({
    queryKey: ['playlist', playlistId],
    queryFn: () => api<PlaylistDetail>(`/playlists/${playlistId}`),
    enabled: playlistId !== null,
  })
}

/** Seconds elapsed, advanced locally between polls.
 *
 * The snapshot arrives every few seconds; a bar that only moved when it did
 * would visibly stutter. This ticks forward from the last known position and is
 * corrected on every fetch, so it can drift by at most one poll interval and is
 * never the source of truth. It stops entirely when paused, and is clamped to
 * the track length so a late snapshot cannot run the bar past the end.
 */
export function useElapsed(player: Player | undefined, fetchedAt: number) {
  const [now, setNow] = useState(() => Date.now())
  const moving = !!player?.playing && !player?.paused
  useEffect(() => {
    if (!moving) return
    const timer = setInterval(() => setNow(Date.now()), 500)
    return () => clearInterval(timer)
  }, [moving, fetchedAt])

  if (!player?.track) return 0
  const base = player.position_s ?? 0
  const drift = moving ? Math.max(0, (now - fetchedAt) / 1000) : 0
  const duration = player.duration_s ?? 0
  const elapsed = base + drift
  return duration > 0 ? Math.min(elapsed, duration) : elapsed
}

export function formatDuration(seconds: number | undefined | null) {
  const total = Math.max(0, Math.floor(seconds ?? 0))
  const hours = Math.floor(total / 3600)
  const minutes = Math.floor((total % 3600) / 60)
  const secs = total % 60
  const pad = (value: number) => String(value).padStart(2, '0')
  return hours > 0 ? `${hours}:${pad(minutes)}:${pad(secs)}` : `${minutes}:${pad(secs)}`
}
