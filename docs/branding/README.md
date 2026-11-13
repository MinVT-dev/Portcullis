# Portcullis brand

**Decision (Min, 2026-11-07):** the AMP 3 edition of the bot is called **Portcullis**. Gatekeeper V2 and V3 keep their names.

![Banner, Discord banner, icons at 72 to 16 px, and the mark and logo on dark and light](preview.png)

## Idea (v3, 2026-11-13)

The mark is a **gate tower**: a solid block with three merlons, a pointed arch cut through it, and the portcullis hanging inside the arch. It replaces the v2 outline arch, which read as thin line-work and blurred into a grid at avatar sizes.

- **Solid, not outlined.** The tower is one filled shape with a sky-to-blue gradient, so it holds up at 16 px and on any background. The arch is navy and the gate is near-white, so the gate is always the brightest thing in the mark.
- **The gate is its own piece.** It hangs 2.5 units inside the arch on a 64-unit grid (three bars, one rail, spiked feet), so the gap between gate and tower still shows at 32 px.
- **Status is built in.** Raised, light shows under the spikes: cyan on the brand icon, green for online. Lowered, the spikes reach the ground and a red sill shows: offline.
- **Clear of CubeCoders.** No cube, no hexagon, no part of the CubeCoders or AMP logos, and a different typeface from theirs. The v2 isometric grid behind the banners is now a plain dot grid.

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
| [portcullis-mark.svg](portcullis-mark.svg) | 512×512 | The full-colour mark on its own, transparent background. Works on dark and light. |
| [portcullis-mark-light.svg](portcullis-mark-light.svg) / [-dark](portcullis-mark-dark.svg) | 512×512 | One-colour mark (near-white or navy) with a see-through arch, for places that take one colour. |
| [portcullis-game-banner.svg](portcullis-game-banner.svg) | 920×430 | Placeholder banner for a game with no art of its own. The PNG is `portcullis/assets/fallback/game-banner.png`; see [GAME_ASSETS.md](../GAME_ASSETS.md). |
| [portcullis-game-logo.svg](portcullis-game-logo.svg) | 512×512 | Placeholder logo for a game with no art of its own (`portcullis/assets/fallback/game-logo.png`): a grey stone tower, so it doesn't read as the bot's own avatar. |

[`generate.py`](generate.py) rebuilds everything: `python3 docs/branding/generate.py docs/branding FONT_DIR --png` from the repo root. It needs `fonttools`, and `cairosvg` for `--png` (which also writes `png/`, `preview.png` and the two fallback PNGs). FONT_DIR holds `Sora[wght].ttf` and `Roboto[wdth,wght].ttf` as they are named in [google/fonts](https://github.com/google/fonts) (`ofl/sora`, `ofl/roboto`). All text is converted to outlines, so the SVGs look the same everywhere and don't need the fonts installed.

## Palette

| Role | Hex |
|---|---|
| Navy (background, the arch) | `#0F1629`, lifted `#18223F` |
| Tower gradient | `#5CE1FF` (sky) to `#4C6BFF` (blue) |
| Gate and text | `#EEF3FB` |
| Secondary text | `#A9B4CB` |
| Lines and pill borders | `#8CAADC` at low opacity |
| Brand light | `#4AD6FF` (cyan) |
| Online / success | `#4ADE80` |
| Offline / error | `#FF5A5F` |

## Type

- **Wordmark:** Sora ExtraBold (SIL Open Font License). It replaces Exo 2, whose squared letters fought the new pointed arch.
- **Text and taglines:** Roboto Regular.

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
