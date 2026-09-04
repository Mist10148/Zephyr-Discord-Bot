# Bot output style

The web has [DESIGN.md](DESIGN.md). This is its counterpart for what Zephyr says
in Discord. It exists because Phase 16 found the bot answering in eleven
different colours with no shared footer, and errors ephemeral in some cogs and
public in others with no stated reason either way.

Two rules, both enforced where they can be.

---

## 1. Every embed comes from the factory

`zephyr/utils/embeds.py`. No cog constructs `discord.Embed` directly, and no cog
names a colour.

**Six roles, indexed by what the message is:**

| Role | Colour | For |
|---|---|---|
| `success` | `#3BA55D` | Something the caller asked for happened |
| `error` | `#ED4245` | It did not happen, and why |
| `warning` | `#FAA61A` | It happened with a caveat, or a hazard advisory |
| `info` | `#5865F2` | A reading, a listing, a state report |
| `neutral` | `#4F545C` | A record with no valence — a moderation case, a history |
| `brand` | `#F0B232` | Zephyr describing itself — `/help`, the join card, the web app card |

The values are Discord's own semantic colours, not `discord.Color`'s named
constants: `discord.Color.green()` is a bright web green that matches nothing in
the client, so an embed built from it sits *on top of* the channel rather than in
it.

Two things follow from having one constructor:

- **The ceilings are enforced once.** Discord answers 400 to an empty field
  value, a title over 256 characters, a description over 4096 or a 26th field.
  Every one of those was a latent failure in whichever cog happened to compute
  one.
- **`set_footer` needs care.** A bare `set_footer` *replaces* the shared footer,
  which is how six call sites silently lost the bot's name and icon. Pass
  `footer=` to the factory, or compose with `embeds.footer_text()` and
  `embeds.icon_url()` when you are stamping an embed you did not build.

`test_embeds.py` covers the factory. `test_embed_style.py` walks the package and
fails if a cog constructs an embed or names a colour itself.

### Choosing an accent

The mistake to avoid is picking by feeling rather than by role. Three that were
in the code before Phase 16:

- **`/stop` was red.** Stopping the player is what the caller asked for. It is
  `success`, not an error.
- **A temperature reading was green.** A reading is not a success; nothing
  succeeded. It is `info`.
- **`/pause` was orange.** Pausing is not a caveat. It is `success`.

`warning` means *a hazard, or a caveat on something that otherwise worked* — a
severe-weather watch, a 24/7 toggle that could not be saved, a queue cleared out
from under people who were listening.

---

## 2. Errors and personal settings are ephemeral; shared state is public

Stated as one sentence so there is something to appeal to in review.

**Ephemeral** — only the caller sees it:

- Every error. A failed `/play` in a busy channel is noise for everybody except
  the person who typed it, and it was the single largest source of channel spam
  before this rule.
- Anything about one person: `/rank` for yourself, `/reminders`, `/export-my-data`,
  `/tag-list`, a case lookup.
- Every settings command. `/dj-only`, `/modlog`, `/welcome`, `/starboard`,
  `/activity` — the person changing a setting needs the confirmation; the channel
  does not.

**Public** — everybody in the channel sees it:

- Changes to shared state, because other people's experience just changed and
  they are entitled to know why the music stopped: `/skip`, `/stop`, `/pause`,
  `/volume`, the effects commands, `/247`.
- Anything the channel asked for collectively: a tag, a starboard entry, a
  greeting, a level-up, a leaderboard, a weather answer.
- Moderation *cases* go to the modlog channel; the moderator's confirmation is
  ephemeral. The action is public by being visible in the server; the
  confirmation is bookkeeping.

The awkward case is worth naming: **a successful settings change disappears from
view** while a failed `/play` used to spam the channel. That is backwards, and it
is the specific thing this rule inverts.

`test_embed_style.py` enforces the half that is mechanically checkable: an
`embeds.error(...)` passed to `send_message` or `followup.send` must carry
`ephemeral=True`.

---

## 3. One command list

`zephyr/utils/help_data.py` is the *prose* — the one-line description of what
each command does, which cannot be derived from anything. The **set** of
commands is derived from `bot.tree` and published over the bridge, so
`GET /commands` and `/help` cannot disagree about which commands exist.

The counts in `README.md` and `docs/PRD.md` are a third copy. `test_help_data.py`
asserts they match the registry, which is what stops the drift that had them
saying 75 while the tree held 114.
