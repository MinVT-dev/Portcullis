# Images for the READMEs

The main [README](../../README.md) uses the pictures in this folder. Some are real captures from a running panel; the rest are **placeholders** that say "Screenshot coming soon".

To replace a placeholder, save the real screenshot as a PNG under **the same file name**. The READMEs pick it up without any changes.

## Real screenshots

| File | Shows |
|---|---|
| `server-list.png` | `/server list` with six servers |
| `help.png` | `/help` for an admin |
| `server-status.png` | A status card for a running server |
| `server-offline.png` | A status card for an offline server |
| `backups.png` | `/backups` with the Make a backup confirmation |
| `alerts.png` | The reply after saving an alert rule |
| `setup-server.png` | The `/setup server` panel for one server |
| `setup-bot.png` | The `/setup bot` panel |
| `help-v3.png` | `/help v3:` suggesting old V3 commands as you type |
| `server-banner-dark.png`, `server-banner-light.png` | Real output from the bot's own `/server banner` renderer (`portcullis/bot/banners.py`), drawn with example servers |

## Mock-ups (sample data)

`embeds/` holds pictures of the cards drawn from the bot's own embed code, using made-up servers and players. They are not captures. Rebuild them from the repo root with:

```sh
python3 tools/render_embeds.py /tmp/embeds_html
NODE_PATH=$(npm root -g) node tools/screenshot_embeds.js /tmp/embeds_html docs/images/embeds
```

| File | Shows |
|---|---|
| `embeds/live-card.png` | A live status card for a running server, with Panel and Support buttons |
| `embeds/live-card-states.png` | The card offline, busy and waiting for the licence |
| `embeds/live-card-fallback.png` | The card with plain emojis, before the brand emojis are uploaded |
| `embeds/diagnostics.png` | `/server diagnostics` |
| `embeds/health.png` | `/health` |
| `embeds/setup-start.png` | `/setup start` |

## Screenshots still to take

| File | What to capture | Shown in the README |
|---|---|---|
| `live-board.png` | The live server board message in its channel, with a few servers listed | Yes |
| `console-relay.png` | A console relay channel with a few batches of console lines | Yes |
| `whitelist-request.png` | A whitelist request in the requests channel, with its Approve and Deny buttons | Yes |
| `events-polls.png` | An event with Going and Cancel, or a poll with its buttons | Not yet |

## Tips

- **Hide personal details before you save.** Blur or crop out:
  - IP addresses and panel addresses;
  - members' names and avatars, unless they agreed;
  - player names;
  - tokens and server ids.
- **Capture:**
  - Use Discord's dark theme.
  - Make the picture about 1200 px wide.
  - Crop to the message itself, without the channel list. Wide pictures work best, because the README shows two to a row.
- **Keep each file under about 500 KB.** To shrink a PNG, run `python -c "from PIL import Image; Image.open('x.png').save('x.png', optimize=True)"`.
- **Don't commit game art or raw recordings.** See [GAME_ASSETS.md](../GAME_ASSETS.md).
