// Hand-mirrored Flask contracts. Every Discord id is a string: snowflakes exceed
// Number.MAX_SAFE_INTEGER, so a number would silently lose precision.
//
// Deliberately absent from MeGuild: the Discord permissions bitfield. Web-side
// permission checks are UX rather than security -- the bot re-validates against its
// live cache before acting -- and /me already returns only manageable guilds, so
// exposing a bitfield would only invite permission maths that must not live here.
export type MeUser = { id: string; username: string; global_name: string | null; avatar: string | null; avatar_url: string }
// bot_present is null, not false, when the bot has never published a snapshot:
// unknown and absent are different states and must render differently.
export type MeGuild = { id: string; name: string; icon: string | null; icon_url: string | null; owner: boolean; bot_present: boolean | null }
export type Me = { user: MeUser; guilds: MeGuild[]; guilds_stale: boolean; bot_snapshot_at: number | null; invite_url: string; csrf_token: string }
export type GuildSettings = { id: string; defaults_applied: boolean; prefix: string; locale: string; timezone: string; default_volume: number; dj_role_id: string | null; music_channel_ids: string[]; enabled_cogs: string[] }
export type GuildOverview = GuildSettings & { name: string; icon: string | null; icon_url: string | null; owner: boolean; bot_present: boolean | null; bot_snapshot_at: number | null; editable: boolean }
// Channels and roles are asked of the bot in real time -- the web tier has no
// gateway connection, so this is the only way it can name a channel id.
export type GuildMeta = { channels: { id: string; name: string; can_send: boolean }[]; roles: { id: string; name: string; managed: boolean }[]; voice_channels: { id: string; name: string }[] }

export type PlayerTrack = { title: string; url: string | null; duration_s: number; requester_id: string | null; requester_mention: string; uploader: string; thumbnail: string | null; source: string }
// `live` is false when the bot is publishing nothing at all -- offline, or not in
// a voice channel here. That is not the same as a live snapshot that happens to be
// idle, and the two must not render identically.
export type Effects = { bass_boost: number | null; pitch: number; nightcore: boolean; vaporwave: boolean; reverb: boolean; slowed: boolean; slownrev: boolean; sixteen_d: boolean }
export type Player = {
  guild_id: string; live: boolean; connected: boolean
  voice_channel_id?: string | null; voice_channel_name?: string | null
  playing?: boolean; paused?: boolean; position_s?: number; duration_s?: number
  loop?: 'off' | 'track' | 'queue'; volume?: number; autoplay?: boolean; always_on?: boolean
  effects?: Effects; track: PlayerTrack | null; queue: PlayerTrack[]
  queue_length?: number; queue_duration_s?: number; published_at?: number
}

export type PlaylistSummary = { id: number; owner_id: string; guild_id: string | null; name: string; is_public: boolean; created_at: string | null; track_count: number; duration_s: number; mine: boolean }
export type PlaylistTrack = { title: string; url: string | null; duration_s: number; source: string }
export type PlaylistDetail = Omit<PlaylistSummary, 'mine'> & { mine: boolean; tracks: PlaylistTrack[] }
