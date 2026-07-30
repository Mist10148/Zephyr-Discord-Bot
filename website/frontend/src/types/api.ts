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

export type SubKind = 'daily' | 'severe' | 'class_suspension'
// A null threshold disables that one check without disabling the subscription --
// "warn me about wind but never about rain".
export type Thresholds = { wind_speed?: number | null; precipitation_probability?: number | null; apparent_temperature?: number | null; storm?: boolean }
export type WeatherSub = {
  id: number; guild_id: string; channel_id: string; kind: SubKind; kind_label: string
  location: string; lat: number; lon: number; units: 'metric' | 'imperial'
  schedule_local_time: string | null; tz: string; thresholds: Thresholds | null
  enabled: boolean; last_run_at: string | null; last_fingerprint: string | null; created_at: string | null
}
export type WeatherSubList = { subscriptions: WeatherSub[]; kinds: SubKind[]; default_thresholds: Required<Thresholds> }
export type AlertField = { name: string; value: string }
export type Alert = { kind: SubKind; title: string; summary: string; fields: AlertField[]; fingerprint: string }
// `alert: null` is the answer for a watch with nothing to report, not an empty
// response. `duplicate` is why a preview can show an alert the channel will not
// receive: the same fingerprint was posted last time.
export type SubPreview = { id: number; kind: SubKind; alert: Alert | null; would_post: boolean; duplicate: boolean }
export type Persona = { id: number; guild_id: string; name: string; system_prompt: string; is_default: boolean; created_at: string | null; updated_at: string | null }
export type AIConversation = { channel_id: string; rolling_summary: string | null; token_count: number; updated_at: string | null; message_count: number }
export type AIUsage = { model: string; rpm: number; tpm: number; rpd: number; cooldown_until: string | null; totals: { prompt_tokens: number; output_tokens: number; total_tokens: number; successful_requests: number; session_requests: number } }
