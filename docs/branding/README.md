# Name and branding ideas

Status: **proposal only.** Nothing has been renamed. Min and k8t pick.

![Preview of the three banner and logo mock-ups](preview.png)

## Do we need a new name?

You only have to rename if one of these is true:

- **Findability matters.** We want the bot listed publicly, on Top.gg or in search, and found by people who never used V2 or V3. "Gatekeeper" is crowded: GitHub has about 45 Discord-bot repos with that word, plus OPA Gatekeeper and Apple's macOS Gatekeeper.
- **We want a clean break.** V2 and V3 users won't confuse the AMP 3 edition with the old ones.

If the bot spreads mostly by word of mouth in the CubeCoders community, "Gatekeeper" already has recognition there that a new name would have to earn from scratch. Keeping the name is a real option, and a refreshed logo may be enough.

## Shortlist

Checked on 2026-11-07 against GitHub repo search, npm, PyPI and a general web search (which also covers Top.gg and Discord bot lists). This was **not** a trademark search, and I could not search Discord's own app directory.

| Name | Verdict | What the checks found |
|---|---|---|
| **Portcullis** | **Recommended if renaming** | No Discord bot or game-server tool found under this name. Other uses: Docker's small Go secret-redaction library (16★), an AI-agent package on PyPI, a bioinformatics tool, a 0★ Discord gateway proxy repo, and "Portcullis Games" on Steam. npm and PyPI names are taken. The downside is spelling: people will type "portcullus". |
| **Gatekeeper** (keep) | Strong option | Keeps V2/V3 recognition. Very crowded and hard to find in search. |
| **Drawbridge** | Possible | Fits the "bridge between game and Discord" idea. But there is a popular "Drawbridge" design tool for Claude Code and Cursor (969★), a Drawbridge Archipelago-to-Discord relay setting in the bridgeipelago bot, and a Death Stranding Discord server called Drawbridge. |
| **Herald** | Weak | Fits join/leave announcements. About 35 Discord-bot repos already use it. |
| ~~Gatehouse~~ | Avoid | **GatehouseMC** is a Minecraft mod with a Discord bot, which is our exact niche. |
| ~~Castellan~~ | Avoid | **Castellan Bot** is a hosted Discord bot for Conqueror's Blade. |

Also rejected: **Warden** (a Valheim server manager uses it), **Turnstile** (Cloudflare), **Barbican** (OpenStack's secrets service), **Postern** (a well-known Android proxy app), **Keepwatch** (a monitoring and alerting product).

**Keep "AMP" out of the name.** It's CubeCoders' trademark. Whether a subtitle like "for AMP 3" is OK is a question for Mike.

## Concepts

All three use the same layout: a rounded-square mark that still reads as a Discord avatar, and a 1280×640 banner (GitHub social-preview size, croppable to Discord's 680×240 profile banner) with the name, a tagline and four feature pills. The colors are a starting point.

### Portcullis ([logo](portcullis-logo.svg), [banner](portcullis-banner.svg))

- **Motif:** an iron portcullis half raised inside an arch, with green light coming through underneath. Raised gate means "server up", which gives an easy status variant: drop the gate and turn the light red when offline.
- **Palette:** iron slate `#1B212B`, steel `#A7B0BF`, signal green `#3DDC84`. Amber `#F5A524` for warnings.
- **Style:** flat, geometric, dark. Friendly to embeds.
- **Weak point:** at 32 px the bars blur into a grid. A final avatar version should use 3 bars, not 5.

### Gatekeeper refresh ([logo](gatekeeper-logo.svg), [banner](gatekeeper-banner.svg))

- **Motif:** a shield with a glowing keyhole, meaning access control and protection. It stays the most readable at small sizes.
- **Palette:** deep navy `#101B33`, shield blue `#1E3A5F`, teal `#2DD4BF`.
- **Style:** clean and modern. Close to a security product look, which suits whitelist and ban features but says less about games.

### Drawbridge ([logo](drawbridge-logo.svg), [banner](drawbridge-banner.svg))

- **Motif:** a castle gate with its bridge lowered and a warm lit doorway, meaning "come in" and "a bridge into your server".
- **Palette:** moat blue `#12283A`, stone `#5B6B78`, timber `#B7793F`, lantern amber `#F5B83D`.
- **Style:** warmer and more playful, closest to the game-server feel.

## Next steps once a name is picked

1. Run a quick trademark check (EUIPO and USPTO) and search Discord's app directory for the chosen name.
2. Tidy the mark for 32 px and 16 px, and make online and offline variants for status embeds.
3. Rename the repo (GitHub redirects the old URL), the bot's display name and the README. Slash commands don't change.

The SVGs here are hand-written mock-ups, not final artwork.
