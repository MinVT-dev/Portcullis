# Portcullis brand

**Decision (Min, 2026-11-07):** the AMP 3 edition of the bot is called **Portcullis**. Gatekeeper V2 and V3 keep their names.

![Banner, Discord banner, icons at 112 to 20 px, and the mark and logo on dark and light](preview.png)

## Idea (v4 "Inside out", chosen by Min 2026-11-13)

The mark is a lowercase **p** folded from one sheet of charcoal card. Two folds show the orange inside of the card: the turned-down top corner and the tail folded out to the side.

- **It's a letter, not a castle.** v2 and v3 drew gates and arches. Min asked to leave that style behind, and picked this from 30 concepts (concept 27).
- **Orange lives only in the folds.** The rest of the mark is warm charcoal, so the colour reads as the inside of the paper, not as decoration. That makes status easy: the folds turn green when a server is online and red when it's offline. The shape never changes.
- **Flat and dark.** There are no gradients and no glow. Every asset is near-black with flat colour, so the avatar looks the same in Discord's light and dark themes.
- **Clear of other brands.** There is no cube, hexagon or X shape, and no part of the CubeCoders, AMP or Proxmox logos. The dark-plus-orange mood is the only thing Proxmox inspired.
- **Wordmark:** `portcullis` in Syne ExtraBold, lowercase, on the same baseline as the bottom of the mark's bowl, so the two p's line up.

## Files

SVG is the source. Upload-ready PNGs are in [`png/`](png/).

| File | PNG | Use it for |
|---|---|---|
| [portcullis-icon.svg](portcullis-icon.svg) | 1024×1024, plus `portcullis-avatar-512.png` | Discord app icon and bot avatar. Flat near-black, full-bleed, and the mark stays inside Discord's circle crop. |
| [portcullis-status-online.svg](portcullis-status-online.svg) | 256×256 | Online status in embeds and thumbnails: green folds. |
| [portcullis-status-offline.svg](portcullis-status-offline.svg) | 256×256 | Offline status: red folds. |
| [portcullis-banner.svg](portcullis-banner.svg) | 1280×640 | GitHub social preview, release posts. |
| [portcullis-discord-banner.svg](portcullis-discord-banner.svg) | 1360×480 | The bot's Discord profile banner (680×240 at 2×). The left third stays plain, because the avatar sits there. |
| [portcullis-logo.svg](portcullis-logo.svg) / [-dark](portcullis-logo-dark.svg) | 264 high | Mark and wordmark, for dark and light backgrounds. |
| [portcullis-mark.svg](portcullis-mark.svg) / [-dark](portcullis-mark-dark.svg) | 512×512 | The mark on its own, transparent. The charcoal works on both dark and light, so the two files are the same; both names are kept so links don't break. |
| [portcullis-game-banner.svg](portcullis-game-banner.svg) | 920×430 | Placeholder banner for a game with no art (`portcullis/assets/fallback/game-banner.png`; see [GAME_ASSETS.md](../GAME_ASSETS.md)). |
| [portcullis-game-logo.svg](portcullis-game-logo.svg) | 512×512 | Placeholder logo for a game with no art (`portcullis/assets/fallback/game-logo.png`). Grey folds, so it doesn't read as the bot's own avatar. |
| [emoji/](emoji/) | 128×128 each | The eight brand emojis (server states, licence, backup, console, players), uploaded as Discord application emojis. See [EMOJIS.md](../EMOJIS.md). Built by `emoji.py` and `render_emoji.js`. |

**Rebuild:**

1. `python3 docs/branding/generate.py docs/branding FONT_DIR` rewrites every SVG. It needs `fonttools`. FONT_DIR holds `Syne[wght].ttf` from Google Fonts; the script makes the ExtraBold and Regular instances itself. All text becomes outlines, so the SVGs don't need the font installed.
2. `node docs/branding/render_png.js` renders every PNG, including the two game fallbacks, with headless Chromium. It needs Playwright.

**Minimum size:** the mark holds up at 20 px. Below that, use the plain wordmark instead.

## Palette

| Role | Hex |
|---|---|
| Night (background) | `#141414` |
| Card (the mark) | `#5C5752` |
| Stone (light background) | `#EFEAE3` |
| Cream (text on dark) | `#EDE6DC` |
| Ash (secondary text, neutral folds) | `#8C857E` |
| Orange (brand folds, the one accent) | `#E8710A` |
| Online / success | `#4ADE80` |
| Offline / error | `#FF5A5F` |

The bot's embed colours in `portcullis/bot/base.py` use the same values: orange for neutral notices, green and red for status, mustard `#F2B33D` for warnings and ash for muted.

## Type

- **Wordmark and text:** Syne ExtraBold and Regular (SIL Open Font License).

**Tagline:** "Your game servers, in Discord." Keep "AMP" and CubeCoders' logos out of all Portcullis artwork, so nothing suggests it's an official CubeCoders product.

**History:** v1 (PR #7) was rejected. v2 (PR #10) was a hexagon gatehouse in cyan. v3 (PR #28) was a pointed gate with a lattice in amber. v4 replaces them all. The 30 concept sheets Min reviewed are outside the repo, in the project files.

## Still to check before going public

- **Trademark search** (EUIPO and USPTO) for "Portcullis" in software classes 9 and 42. Not done yet.
- **Discord app directory:** search for an existing app called Portcullis. Not done yet.
- Known other uses, none of them a Discord bot or game-server tool: Docker's Go secret-redaction library `docker/portcullis`, the PyPI package `portcullis` (an AI-agent policy layer), a bioinformatics tool, a 0★ Discord gateway proxy repo `ShadyDonkey/portcullis`, and Portcullis Games on Steam. The `portcullis` name is taken on both npm and PyPI, so a published package would need another name, such as `portcullis-bot`.

## Rename status

- **Done:** the GitHub repo is now [`MinVT-dev/Portcullis`](https://github.com/MinVT-dev/Portcullis) (renamed by Min, 2026-11-07). The old `gatekeeper-AMP-3` URL redirects.
- **Done in this PR:** every mention of the new bot in the docs now says Portcullis. That covers titles, the package layout `portcullis/`, the "Apps → Portcullis user" context menu, the suggested AMP role, and decision D-2 (closed).
- **Kept on purpose:** Gatekeeper V2, Gatekeeper V3, `GatekeeperV2`, `GatekeeperV3-Beta`, the `gatekeeperv2` AMP template, and lines that describe what V2/V3 did. Those are history.
- **Still to do outside the repo:** the Discord application name, the bot username, the icon and the profile banner in the Developer Portal, and the AMP 3 role and bot user (name both `Portcullis`).
- **Code:** the Python package is `portcullis` from the first commit.
