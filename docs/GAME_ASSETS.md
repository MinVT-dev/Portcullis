# Game art

Portcullis shows a banner and a logo for the game behind each server: in status embeds, server lists and alerts. This page explains where that art comes from, how much of it there is, and what is still open.

**As of 2026-11-07.** Built from `docs/AMP2_SUPPORTED.md`, `CubeCoders/AMPTemplates` at commit `93b03cc` (2026-11-06) and Steam's store API.

## Coverage

229 games: the 225 Generic-module games in [AMP2_SUPPORTED.md](AMP2_SUPPORTED.md), the 2 dedicated modules (Minecraft Java Edition and Rust), and 2 templates added to AMPTemplates after that list was made (Everlast: Undying Tale, Halo 3 (Project Reclaimer Mod)). The 19 services (databases, app runners, voice servers) are in the registry too, so a lookup never fails, but they are not counted here.

"Supported" means the 161 games Portcullis plans to give player tracking: the 159 templates with join and leave patterns, plus Minecraft Java and Rust.

| | All 229 games | 161 supported games |
|---|---|---|
| Banner (wide image) | 207 | 142 |
| Logo | 214 | 151 |
| Both a banner and a logo | 194 | 134 |
| Small square icon | 198 | 138 |
| No art at all | 2 | 2 |

With the placeholder images below, every game gets a banner and a logo. The numbers come from the manifest itself (`coverage` in `games.json`), and every URL in it was fetched once on the date above and answered with an image.

Where the art came from: 205 games from Steam, 22 from the image link in the AMP template, 2 with none.

**Supported games with no real banner (19)**, which use the placeholder banner: Cube 2: Sauerbraten, Hytale, Luanti, Minecraft Bedrock, Minecraft Java Edition, Minetest (Legacy), Nukkit, OpenRA (Dune 2000, Red Alert, Tiberian Dawn), OpenStarbound, Path of Titans, PocketMine-MP, Pre-Fortress 2, Space Engineers (Torch), Veloren, Vintage Story (Legacy), Vintage Story 1.18.8+, Xonotic. Most are not on Steam, and the template only links a logo or a square icon.

**Supported games with no logo (10)**, which use the icon or the placeholder: Alchemy Factory, Call of Duty: Modern Warfare 2 (2009), Counter-Strike: Global Offensive, Half-Life Deathmatch: Source, Just Cause 3 Multiplayer Mod, Minecraft Bedrock, Minecraft Java Edition, Pumpkin, RuneScape: Dragonwilds, STAR WARS Jedi Knight - Jedi Academy. Steam has no library logo for these.

**No art at all (2):** Minecraft Java Edition (a dedicated module, no template) and Minecraft Bedrock (the template uses `internal:MinecraftBedrock`, an image bundled inside the AMP panel that we can't link to).

## Where the art comes from

1. **The template's own image.** Every AMP template has `Meta.DisplayImageSource`, the picture AMP itself shows for the game. Across the 246 templates it is `steam:<appid>` for 195, a direct link for 50, and `internal:` for one. AMP 3 copies it onto every shard as `displayImageSource` (see `DeployedShard` in [amp3-api/API_REFERENCE.md](amp3-api/API_REFERENCE.md)), so Portcullis can find a shard's game from the shard record alone.
2. **Steam's store API** (`IStoreBrowseService/GetItems`, no key) for every Steam app id. It returns the exact URLs of the header (our banner, 460×215), library logo, library hero, library capsule and community icon. Newer Steam apps only have hashed asset paths, so these URLs can't be built from a pattern. The builder asks for the game's normal art, not seasonal sale art, and falls back to Steam's older fixed paths (`header.jpg`, `logo.png`) when the API has nothing.
3. **Portcullis placeholders** for anything left over.

The template's link fills a gap Steam leaves. A transparent image is used as a logo (wordmarks can be wide), an opaque wide image is used as a banner, and anything else is a logo.

## Placeholders

Two images in the brand style ([branding/README.md](branding/README.md)). They have no text, so one image serves every game and the embed title carries the game's name.

| File | Size | Use |
|---|---|---|
| `portcullis/assets/fallback/game-banner.png` ([SVG](branding/portcullis-game-banner.svg)) | 920×430 | Banner for a game with none. The same shape as a Steam header at 2×, so embeds line up. |
| `portcullis/assets/fallback/game-logo.png` ([SVG](branding/portcullis-game-logo.svg)) | 512×512 | Logo or thumbnail for a game with none. The brand icon with a neutral grey glow, so it doesn't read as the bot's own avatar. |

They ship inside the package. `docs/branding/generate.py` rebuilds the SVGs with the rest of the brand.

## Using it in code

```python
from portcullis.assets import game_art

art = game_art(template_name, image_source=shard.display_image_source, name=shard_display_name)
art.banner_url      # wide image URL, or None
art.thumbnail_url   # logo, else icon, or None
art.banner_file     # the placeholder PNG, for when banner_url is None
art.logo_file       # the placeholder PNG, for when thumbnail_url is None
art.supported       # True for the 161 games with player tracking
```

`game_art()` looks the game up by template name, then display name (ignoring case and punctuation), then the AMP image source, and always returns something. A game that is not in the manifest yet (a template CubeCoders added later) still gets Steam's standard header and logo URLs from its `steam:<appid>`, or its template link, with `known=False`. `find_game()` is the same lookup but returns None for an unknown game.

For a Discord embed, use the URL when there is one. Otherwise attach the placeholder (`discord.File(art.banner_file, filename="banner.png")`) and set the image to `attachment://banner.png`. Discord fetches and caches remote images itself, so the bot never downloads them at runtime.

Which AMP 3 shard field holds the template name is not confirmed yet. `displayImageSource` is, so passing it as `image_source` always works.

## Rebuilding the manifest

```
git clone --depth 1 https://github.com/CubeCoders/AMPTemplates.git /tmp/AMPTemplates
python tools/build_game_assets.py --templates /tmp/AMPTemplates --verify
```

It takes under a minute. `--verify` fetches every URL and keeps only those that return an image. Re-run it when the games list in AMP2_SUPPORTED.md changes, when CubeCoders adds templates, or every few months: Steam's hashed URLs change when a publisher updates their art. Old ones have kept working so far, but nothing promises that. The test `test_every_documented_game_is_in_the_manifest` fails when the docs list a game the manifest lacks.

## Local copies (optional)

```
python -m tools.fetch_game_assets                    # banner and logo for every game
python -m tools.fetch_game_assets --supported --slots banner,logo,icon
```

Images go to `game-art/<template>/<slot>.<ext>`, which is git-ignored. `GameArt.cached(slot, cache_dir)` finds them. Use this to look the art over, to work offline, or to host the images yourself.

## Licence and trademarks

**Decision for Min:** the repo holds links to the art, not the art itself. That is the recommended setup, and nothing in this change commits a game image.

- The banners and logos belong to each game's publisher. They are trademarks and copyrighted images, and none of them is licensed to Portcullis. AGPL-3.0 does not cover them, and putting them in this repo would not change that.
- Showing a game's own store art to say which game a server runs is how AMP, Steam and most server-list sites use it. Linking to the publisher's CDN keeps us in that position: we don't copy, alter or redistribute the files, and when a publisher changes or withdraws art, Portcullis follows.
- Committing about 200 images would make Portcullis a redistributor of them. If you want local copies anyway (for example to stop depending on Steam's CDN), a per-install cache (the fetch script above) is the safer option than the public repo.
- Never put game art into Portcullis' own branding (avatar, banner, README art). The placeholders contain no game or CubeCoders material.
- This is a judgement, not legal advice. Check it again before Portcullis goes public, alongside the trademark search in [branding/README.md](branding/README.md).

## Still to check

- Whether Discord shows every image here. Steam's CDN, GitHub, Wikimedia and the game sites are public HTTPS links and should work, but not every Discord client shows WebP and SVG. Two logos are SVG (Luanti, Minetest (Legacy)) and two are WebP (Hytale, Pre-Fortress 2).
- The icon is Steam's community icon, which is 32×32 for every game here. It fits an embed author or footer line, not a thumbnail, so `thumbnail_url` only falls back to it when there is no logo.
- The AMP 3 field that names a shard's template, so the lookup can use it before falling back to the image source.

## Keeping the links alive

The registry holds links, and Steam's paths carry a content hash, so a link dies when a publisher uploads new art. `python tools/check_game_art.py` fetches every link and lists the dead ones (all 1,037 answered on 2026-11-11). The "Game art links" workflow runs it every Monday and on any change to `games.json`. A host that refuses the checker itself (Wikimedia does this to CI runners) is listed as blocked and doesn't fail the run. When it fails, rebuild the registry with `tools/build_game_assets.py --templates <dir> --verify`. A dead link is never fatal: Discord shows the embed without its image.
