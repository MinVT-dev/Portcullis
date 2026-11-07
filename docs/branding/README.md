# Portcullis brand

**Decision (Min, 2026-11-07):** the AMP 3 edition of the bot is called **Portcullis**. Gatekeeper V2 and V3 keep their names.

![Banner, Discord banner, logo, icon and offline status at 128, 64, 32 and 16 px](preview.png)

## Files

| File | Size | Use it for |
|---|---|---|
| [portcullis-logo.svg](portcullis-logo.svg) | 512×512 | Main mark: README, docs, the 512 px Discord app icon. Five bars. |
| [portcullis-icon.svg](portcullis-icon.svg) | 512×512 | Small-size cut with three thicker bars. Use it at 64 px and below (bot avatar in member lists, favicons), and as the **online** status image. |
| [portcullis-status-offline.svg](portcullis-status-offline.svg) | 512×512 | **Offline** status: the gate is down and the light turns red. Embed thumbnails and status messages. |
| [portcullis-banner.svg](portcullis-banner.svg) | 1280×640 | GitHub social preview, release posts, website header. |
| [portcullis-discord-banner.svg](portcullis-discord-banner.svg) | 680×240 | The bot's Discord profile banner. The left third stays empty because Discord puts the avatar there. |
| [preview.png](preview.png) | | Contact sheet of all of the above. |

Discord and GitHub need PNG uploads, so export the SVGs at 2× (for example `rsvg-convert -w 1024 portcullis-logo.svg -o logo.png`, or open the file in a browser and save it).

## Palette

| Role | Hex |
|---|---|
| Background (top to bottom) | `#2A3240` → `#141921` |
| Banner background | `#1B212B` → `#0E1218` |
| Iron (bars) | `#A7B0BF` |
| Frame and sill | `#C9D1DD` |
| Online / brand accent | `#3DDC84` |
| Offline / error | `#F0524F` |
| Warning (embeds only) | `#F5A524` |
| Text on dark | `#E6EBF2`, secondary `#9AA6B8` |

Embed colours: green for online and success, red for offline and errors, amber for warnings. The banners use a system sans stack (Segoe UI, Inter, Helvetica Neue, Arial). Turn the text into outlines before final export if the font has to match exactly everywhere.

**Tagline:** "Your game servers, in Discord." Keep "AMP" out of the name and the logo; it's CubeCoders' trademark. Whether "Portcullis for AMP 3" is OK as a descriptive subtitle is a question for Mike.

## Still to check before going public

- **Trademark search** (EUIPO and USPTO) for "Portcullis" in software classes 9 and 42. Not done yet.
- **Discord app directory:** search for an existing app called Portcullis. Not done yet.
- Known other uses, none of them a Discord bot or game-server tool: Docker's Go secret-redaction library `docker/portcullis`, the PyPI package `portcullis` (an AI-agent policy layer), a bioinformatics tool, a 0★ Discord gateway proxy repo `ShadyDonkey/portcullis`, and Portcullis Games on Steam. The `portcullis` name is taken on both npm and PyPI, so a published package would need another name, such as `portcullis-bot`.

## Rename status

- **Done:** the GitHub repo is now [`MinVT-dev/portcullis`](https://github.com/MinVT-dev/portcullis) (renamed by Min, 2026-11-07). The old `gatekeeper-AMP-3` URL redirects.
- **Done in this PR:** every mention of the new bot in the docs now says Portcullis. That covers titles, the package layout `portcullis/`, the "Apps → Portcullis user" context menu, the suggested AMP role, and decision D-2 (closed).
- **Kept on purpose:** Gatekeeper V2, Gatekeeper V3, `GatekeeperV2`, `GatekeeperV3-Beta`, the `gatekeeperv2` AMP template, and lines that describe what V2/V3 did. Those are history.
- **Still to do outside the repo:** the Discord application name, the bot username, the icon and the profile banner in the Developer Portal, and the AMP 3 role and bot user (name both `Portcullis`).
- **Code:** the Python package is `portcullis` from the first commit.
