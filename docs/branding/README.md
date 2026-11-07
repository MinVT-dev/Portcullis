# Portcullis brand

**Decision (Min, 2026-11-07):** the AMP 3 edition of the bot is called **Portcullis**. Gatekeeper V2 and V3 keep their names.

![Banner, Discord banner, icons at 128 to 16 px, and the mark and logo on dark and light](preview.png)

## Idea

The mark is a gatehouse arch with its portcullis hanging inside it.

- **Fits next to CubeCoders, without copying them.** It borrows their visual language: deep navy, flat near-white shapes, one heavy stroke weight, hard mitred corners, and hexagon geometry. The arch's shoulders are the top half of a flat-topped hexagon at 60°, and the banner background is a faint isometric grid. It uses no cube, no hexagon badge, and no part of the CubeCoders or AMP logos. The wordmark is a different typeface from theirs.
- **Built on a grid.** It sits on a 64-unit grid. Every member (frame, beam, bars, rail) is 5 units thick, and there is a 3-unit gap between the frame and the gate, so the two still read apart at 32 px.
- **Status is built in.** With the gate raised, light shows underneath: cyan on the brand icon, green for online. With the gate down, the bars reach the ground and a red sill shows: offline.

## Files

SVG is the source. Upload-ready PNGs are in [`png/`](png/).

| File | PNG | Use it for |
|---|---|---|
| [portcullis-icon.svg](portcullis-icon.svg) | 1024×1024 | Discord app icon and bot avatar. It's full-bleed and stays inside Discord's circle crop. |
| [portcullis-status-online.svg](portcullis-status-online.svg) | 256×256 | Online status in embeds and thumbnails. |
| [portcullis-status-offline.svg](portcullis-status-offline.svg) | 256×256 | Offline status. |
| [portcullis-banner.svg](portcullis-banner.svg) | 1280×640 | GitHub social preview, release posts. |
| [portcullis-discord-banner.svg](portcullis-discord-banner.svg) | 1360×480 | The bot's Discord profile banner (680×240 at 2×). The left third stays plain, because the avatar sits there. |
| [portcullis-logo.svg](portcullis-logo.svg) / [-dark](portcullis-logo-dark.svg) | 1200 wide | The mark and wordmark together, for dark and light backgrounds. |
| [portcullis-mark.svg](portcullis-mark.svg) / [-dark](portcullis-mark-dark.svg) | 512×512 | The mark on its own, with a transparent background. |
| [portcullis-game-banner.svg](portcullis-game-banner.svg) | 920×430 | Placeholder banner for a game with no art of its own. The PNG is `portcullis/assets/fallback/game-banner.png`; see [GAME_ASSETS.md](../GAME_ASSETS.md). |
| [portcullis-game-logo.svg](portcullis-game-logo.svg) | 512×512 | Placeholder logo for a game with no art of its own (`portcullis/assets/fallback/game-logo.png`). |

[`generate.py`](generate.py) rebuilds every SVG: `python3 generate.py OUT_DIR FONT_DIR` (it needs `fonttools` and `brotli`). FONT_DIR holds Exo 2 Bold and Roboto Regular from Google Fonts as `exo2.woff2` and `roboto.woff2`. All text is converted to outlines, so the SVGs look the same everywhere and don't need the fonts installed.

## Palette

| Role | Hex |
|---|---|
| Navy (background) | `#121A30`, lifted `#1A2442` |
| Mark and text | `#E9EFF8` |
| Secondary text | `#A6B2C9` |
| Lines and pill borders | `#8CAADC` at low opacity |
| Brand accent | `#4AD6FF` (cyan) |
| Online / success | `#4ADE80` |
| Offline / error | `#FF5A5F` |

## Type

- **Wordmark:** Exo 2 Bold (SIL Open Font License).
- **Text and taglines:** Roboto Regular, which is also what CubeCoders' site uses for body text.

**Tagline:** "Your game servers, in Discord." Keep "AMP" and CubeCoders' logos out of all Portcullis artwork, so nothing suggests it's an official CubeCoders product. Whether "Portcullis for AMP 3" is OK as a descriptive subtitle is a question for Mike.

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
