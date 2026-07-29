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
export type GuildOverview = { id: string; name: string; icon: string | null; icon_url: string | null; owner: boolean; bot_present: boolean | null; bot_snapshot_at: number | null; defaults_applied: boolean; editable: boolean; prefix: string; locale: string; timezone: string; default_volume: number; dj_role_id: string | null; music_channel_ids: string[]; enabled_cogs: string[] }
