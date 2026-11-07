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

## Where the old name appears

The repo still says "Gatekeeper" in 60 places. Most of them are history: V2 and V3 really are called Gatekeeper and should stay that way. These are the places to change:

**Outside the repo (Min decides):**
- **GitHub repo name** `MinVT-dev/gatekeeper-AMP-3`. GitHub redirects the old URL and old clones keep working. Suggested new name: `portcullis`.
- **Discord application and bot username** in the Developer Portal. Upload the logo as the app icon and the Discord banner as the profile banner there.
- **AMP 3 role and bot user** that the bot signs in as. Name them `Portcullis` from the start.

**In the repo** (one small docs PR once the repo name is settled):
- `README.md:1`, the title "Gatekeeper for AMP 3".
- `docs/DESIGN.md:1`, the title, plus product mentions throughout (about 25 lines). The ones that matter most are the package layout `gatekeeper/` (line 173) and `gatekeeper.amp` (line 201), the "Apps → Gatekeeper user" context menu (lines 156 and 438), the error text at line 215, and the "Gatekeeper role" at line 224. Decision D-2 (line 336), about the name, can now be closed as Portcullis.
- `docs/BETA_CHECKLIST.md`: lines 69, 72 and 119, including the `gatekeeper/amp/` path.
- `docs/AMP2_SUPPORTED.md`: lines 3, 307, 333, 335 and 337. Lines 48, 298 and 309 are V2/V3 history and stay.
- `docs/amp3-api/README.md:3` and `docs/amp3-api/API_SURFACE.md` (lines 42, 49, 59, 112 and 132).
- Code: name the Python package `portcullis` from the first commit, so it never has to be renamed.

Leave references to Gatekeeper V2, `GatekeeperV2`, `GatekeeperV3-Beta` and the `gatekeeperv2` AMP template as they are.
