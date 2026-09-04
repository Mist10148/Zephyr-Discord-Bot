import { useQuery } from '@tanstack/react-query'
import { api } from '../lib/api'
import { GlassSurface, LargeTitleHeader, ListGroup, ListRow, SectionLabel, Skeleton } from '../components/ios'
import { ErrorNote } from '../components/ErrorNote'

// Discord requires both a privacy policy and terms for app verification, and
// the service genuinely processes personal data: Discord user ids, per-channel
// AI conversation memory, and audit rows carrying an actor id.
//
// The retention table is *fetched*, not written here. It lives beside
// zephyr/db/personal_data.delete, which is the code that implements it -- a
// published policy describing a deletion path has to match the path that
// actually runs, and two independently edited copies would drift.

type Legal = {
  retention: { category: string; detail: string }[]
  session_caveat: string
  contact: string | null
  deletion: { self_service: string[]; per_channel: string[] }
}

function useLegal() {
  return useQuery({
    queryKey: ['legal'],
    queryFn: () => api<Legal>('/legal'),
    staleTime: 60 * 60_000,
  })
}

export function Privacy() {
  const legal = useLegal()

  return <main className="app narrow prose">
    <LargeTitleHeader title="Privacy" subtitle="What Zephyr stores, why, and how to remove it." />

    <GlassSurface tier="thin">
      <p>
        Zephyr stores the minimum it needs to answer you. It does <strong>not</strong> store
        Discord access or refresh tokens, does not read messages it has not been
        mentioned in, and does not sell or share anything with anyone.
      </p>
    </GlassSurface>

    <SectionLabel>What is stored</SectionLabel>
    {legal.isPending && <Skeleton variant="rows" count={6} />}
    {legal.isError && <ErrorNote error={legal.error} onRetry={() => legal.refetch()} />}
    {legal.data && (
      <ListGroup>
        {legal.data.retention.map(item => (
          <ListRow key={item.category} label={item.category} detail={item.detail} />
        ))}
      </ListGroup>
    )}

    <SectionLabel>Removing your data</SectionLabel>
    <GlassSurface tier="thin">
      <p>
        Two commands, in any server Zephyr is in or in a direct message:
      </p>
      <ul>
        <li><code>/export-my-data</code> — sends you everything stored about you, as a file.</li>
        <li><code>/delete-my-data</code> — permanently deletes it.</li>
        <li><code>/forget</code> — clears one channel's AI memory, without touching anything else.</li>
      </ul>
      <p className="muted">
        Two things a deletion deliberately keeps. A server's audit history stays for
        the server owner, with your id removed rather than the entry. And an AI
        conversation belongs to a channel and holds several people's messages, so
        only your own messages go — deleting the conversation would erase
        everybody else's.
      </p>
    </GlassSurface>

    <SectionLabel>Sessions</SectionLabel>
    <GlassSurface tier="thin">
      <p className="muted">{legal.data?.session_caveat ?? 'Loading…'}</p>
    </GlassSurface>

    {legal.data?.contact && (
      <>
        <SectionLabel>Contact</SectionLabel>
        <GlassSurface tier="thin">
          <p>
            Anything not covered by the commands above:{' '}
            <a href={legal.data.contact} rel="noreferrer">the support server</a>.
          </p>
        </GlassSurface>
      </>
    )}
  </main>
}

export function Terms() {
  return <main className="app narrow prose">
    <LargeTitleHeader title="Terms" subtitle="The short version, because it is a Discord bot." />

    <SectionLabel>Using Zephyr</SectionLabel>
    <GlassSurface tier="thin">
      <ul>
        <li>Zephyr is provided as-is, with no warranty and no uptime guarantee. It runs on free and low-cost hosting and will occasionally be unavailable.</li>
        <li>Adding Zephyr to a server requires the Manage Server permission, and doing so accepts these terms on that server's behalf.</li>
        <li>Server settings — the prefix, DJ role, music channels, AI channels and weather subscriptions — are changeable by anyone with Manage Server.</li>
      </ul>
    </GlassSurface>

    <SectionLabel>What is not allowed</SectionLabel>
    <GlassSurface tier="thin">
      <ul>
        <li>Automating Zephyr to bypass its rate limits, or using it to place load on Discord, YouTube, Spotify or any other service it talks to.</li>
        <li>Using the AI features to generate content that breaks Discord's Terms of Service or Community Guidelines.</li>
        <li>Reselling access to Zephyr, or presenting it as your own service.</li>
      </ul>
      <p className="muted">
        Zephyr may be removed from a server, or a person's access to it withdrawn,
        for any of the above.
      </p>
    </GlassSurface>

    <SectionLabel>Third-party services</SectionLabel>
    <GlassSurface tier="thin">
      <p>
        Zephyr passes your requests to services it does not control: Discord,
        Open-Meteo and OpenWeatherMap for weather, YouTube and Spotify for music,
        and Google Gemini for AI. Their terms apply to that part of the request,
        and an AI reply is generated by a model rather than written by a person —
        treat it as such.
      </p>
    </GlassSurface>

    <SectionLabel>Changes</SectionLabel>
    <GlassSurface tier="thin">
      <p className="muted">
        These terms may change. The current version is always the one on this
        page, and continuing to use Zephyr accepts it.
      </p>
    </GlassSurface>
  </main>
}
