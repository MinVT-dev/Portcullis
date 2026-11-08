# Images for the READMEs

The main [README](../../README.md) uses the pictures in this folder. Most of them are **placeholders** that say "Screenshot coming soon".

To replace a placeholder, save the real screenshot as a PNG under **the same file name**. The READMEs pick it up without any changes.

## Screenshots to take

| File | What to capture | Shown in the README |
|---|---|---|
| `server-status.png` | `/server status` on a running server: the embed with state, players, CPU, memory and uptime, and the Start, Stop and Restart buttons plus a quick command | Yes |
| `live-board.png` | The live server board message in its channel, with a few servers listed | Yes |
| `console-relay.png` | A console relay channel with a few batches of console lines | Yes |
| `whitelist-request.png` | A whitelist request in the requests channel, with its Approve and Deny buttons | Yes |
| `setup-server.png` | The `/setup server` panel: the relay pickers and the buttons below them | Yes |
| `alerts.png` | An alert rule firing in its channel, or `/alert list` with Mute, Unmute and Delete | Yes |
| `server-list.png` | `/server list players:True` with several servers and player names | Not yet |
| `setup-bot.png` | The `/setup bot` panel | Not yet |
| `events-polls.png` | An event with Going and Cancel, or a poll with its buttons | Not yet |
| `help-v3.png` | `/help v3:` for an old Gatekeeper V3 command | Not yet |

## Real images, not placeholders

- **`server-banner-dark.png` and `server-banner-light.png`** are real output from the bot's own `/server banner` renderer (`portcullis/bot/banners.py`), drawn with example servers. You can replace them with a capture from your own panel.

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
