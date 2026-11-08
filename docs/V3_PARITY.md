# Gatekeeper V3 commands in Portcullis

<!-- Generated from portcullis/bot/v3map.py by render_markdown(). Edit that file, not this one. -->

Gatekeeper V3 had 117 slash commands. This table says where each one went. In Discord, `/help v3:<old command>` gives the same answer.

Portcullis folds them into far fewer commands on purpose: settings live in `/setup` panels, actions on a thing are buttons on that thing, and choices are options. The jobs are kept; the command names mostly aren't.

- **Same name:** 12
- **Moved** (another command, option, button or panel field): 78
- **Not yet** (waiting on AMP 3 data seen on a real panel): 2
- **Dropped** (with the reason): 25

| V3 command | Status | In Portcullis |
|---|---|---|
| `/bot donator` | Moved | /user → Toggle donator (also on right-click → Apps → Portcullis user) |
| `/bot moderator` | Moved | Operator roles in .env (PORTCULLIS_OPERATOR_ROLE_IDS), plus /setup bot → Who can do what (owners) |
| `/bot permissions` | Moved | /setup bot → Who can do what (owners), and /setup server → Server operators (owners) |
| `/bot settings` | Moved | /setup bot |
| `/bot utils message_timeout` | Dropped | Portcullis's replies are private to the member who asked, so there is nothing to clear up later. |
| `/bot utils clear` | Dropped | Discord's own moderation tools do this, and a purge command is a risk. |
| `/bot utils roleid` | Dropped | Discord's developer mode already shows role, channel and user ids (right-click → Copy ID). |
| `/bot utils channelid` | Dropped | Discord's developer mode already shows role, channel and user ids (right-click → Copy ID). |
| `/bot utils userid` | Dropped | Discord's developer mode already shows role, channel and user ids (right-click → Copy ID). |
| `/bot utils steamid` | Moved | /player id (Steam ID formats) |
| `/bot utils uuid` | Moved | /player id (Minecraft UUID lookup) |
| `/bot utils ping` | Moved | /admin bot action:Status (owners) |
| `/bot utils disconnect` | Moved | /admin bot action:Stop (owners) |
| `/bot utils restart` | Moved | /admin bot action:Stop (owners); your service manager starts it again |
| `/bot utils status` | Moved | /admin bot action:Status (owners) |
| `/bot utils sync` | Moved | /admin bot action:Sync (owners) |
| `/bot cog load` | Dropped | Portcullis has no plug-in cogs. Restart it to load new code. |
| `/bot cog unload` | Dropped | Portcullis has no plug-in cogs. Restart it to load new code. |
| `/bot cog reload` | Dropped | Portcullis has no plug-in cogs. Restart it to load new code. |
| `/server start` | Moved | /server power action:Start, or the Start button on /server status |
| `/server stop` | Moved | /server power action:Stop, or the Stop button on /server status |
| `/server restart` | Moved | /server power action:Restart (or Restart with a warning in game chat) |
| `/server kill` | Not yet | AMP 3.0.0.6 has a kill permission but no API route for it yet. |
| `/server msg` | Moved | /server send as:In-game chat |
| `/server broadcast` | Moved | /server send as:In-game chat |
| `/server status` | Same name | /server status |
| `/server users` | Moved | /server status (Online now), or /server list players:True |
| `/server update` | Dropped | Portcullis re-reads AMP's server list every 5 minutes. For a game update, use /server power action:Update the game. |
| `/server backup` | Moved | /backups |
| `/server settings info` | Moved | /setup server |
| `/server settings avatar` | Dropped | Server art comes from AMP's template and the game registry. |
| `/server settings displayname` | Moved | /setup server → Name and visibility |
| `/server settings host` | Moved | /setup server → Options → Address players connect to |
| `/server settings donator` | Dropped | Donator-only servers aren't carried over. The donator flag is on /user. |
| `/server settings role` | Moved | /setup server → Server operators (owners) |
| `/server settings prefix` | Moved | /setup server → Name and visibility → Chat prefix |
| `/server settings hidden` | Moved | /setup server → Name and visibility → Hide from members |
| `/server console channel` | Moved | /setup relay kind:Console, or the channel pickers on /setup server |
| `/server console denylist` | Moved | /setup server → Console rules → Blocked console commands |
| `/server console rate_limit` | Dropped | Relays batch lines and drop the oldest when a channel can't keep up, so there is nothing to tune. |
| `/server console filter` | Moved | /setup server → Console rules → Hidden console lines |
| `/server chat channel` | Moved | /setup relay kind:Chat |
| `/server event channel` | Moved | /setup relay kind:Events |
| `/server regex add` | Moved | /setup server → Console rules → Hidden console lines (start a line with re: for a pattern) |
| `/server regex delete` | Moved | /setup server → Console rules → Hidden console lines |
| `/server regex list` | Moved | /setup server → Console rules (the panel shows the current rules) |
| `/regex_pattern add` | Moved | /setup server → Console rules → Hidden console lines |
| `/regex_pattern delete` | Moved | /setup server → Console rules → Hidden console lines |
| `/regex_pattern update` | Moved | /setup server → Console rules → Hidden console lines |
| `/regex_pattern list` | Moved | /setup server → Console rules (the panel shows the current rules) |
| `/dbserver cleanup` | Dropped | Portcullis syncs its server list with AMP on its own and marks servers that are gone. |
| `/dbserver change_instance_id` | Dropped | AMP 3 shard ids never change. |
| `/user info` | Same name | /user |
| `/user add` | Moved | /user (members are added the first time they use Portcullis) |
| `/user update` | Moved | /user → its buttons (donator, blocked from requests, links) |
| `/role` | Moved | /setup bot → Who can do what (owners) |
| `/alert create` | Same name | /alert create |
| `/alert list` | Same name | /alert list |
| `/alert delete` | Moved | Delete button on /alert list |
| `/alert mute` | Moved | Mute button on /alert list |
| `/alert channel` | Moved | /setup bot → Alert and log channels |
| `/audit log` | Moved | /audit |
| `/audit search` | Moved | /audit with its member, server, action and result filters |
| `/audit stats` | Moved | /audit stats:True |
| `/audit export` | Moved | /audit export:True |
| `/audit channel` | Moved | /setup bot → Alert and log channels |
| `/backup list` | Moved | /backups |
| `/backup create` | Moved | Make a backup button on /backups |
| `/backup delete` | Moved | Delete button on /backups |
| `/bannergroup create_group` | Moved | /setup bot → the live server board (one self-updating message for every server) |
| `/bannergroup info` | Moved | /setup bot → the live server board |
| `/bannergroup rename` | Dropped | There is one board, so it needs no name. |
| `/bannergroup add` | Moved | /setup bot → the live server board (it lists every server that isn't hidden) |
| `/bannergroup remove` | Moved | /setup server → Name and visibility → Hide from members |
| `/bannergroup delete_group` | Moved | /setup bot → Turn the server board off |
| `/banner background` | Moved | /server banner (an image card with the game's art) |
| `/banner settings` | Moved | /server banner |
| `/banner_settings auto_update` | Moved | /setup bot → the live server board (it updates itself) |
| `/banner_settings type` | Moved | /setup bot → board style (embed or image) |
| `/banner_settings auto_remove` | Dropped | The board edits one message in place, so there is nothing to remove. |
| `/banner_settings timeformat` | Dropped | Discord shows every time in each reader's own format. |
| `/banner_settings timezone` | Dropped | Discord shows every time in each reader's own time zone. |
| `/config export` | Moved | /admin export |
| `/config import` | Moved | /admin import |
| `/diagnostics` | Moved | /admin diagnostics |
| `/help` | Same name | /help |
| `/about` | Moved | /help |
| `/link start` | Moved | /link server:<server> name:<your name> |
| `/link status` | Moved | /link (with no options it shows your links) |
| `/link clear` | Moved | /link → Remove my links |
| `/player channel` | Moved | /setup relay kind:Events |
| `/player online` | Moved | /server list players:True |
| `/player lookup` | Same name | /player lookup |
| `/player top` | Same name | /player top |
| `/poll create` | Same name | /poll create |
| `/poll list` | Same name | /poll list |
| `/poll close` | Moved | Close poll button on the poll |
| `/poll results` | Moved | The poll message shows live counts |
| `/poll delete` | Dropped | Close the poll, then use Discord's own Delete Message if you want it gone. |
| `/schedule create` | Not yet | AMP 3's schedule step and trigger formats haven't been seen on a real panel yet. Create it in AMP for now. |
| `/schedule list` | Same name | /schedule list |
| `/schedule delete` | Moved | Delete button on /schedule list |
| `/schedule pause` | Moved | Off button on /schedule list |
| `/schedule resume` | Moved | On button on /schedule list |
| `/schedule run` | Moved | Run now button on /schedule list |
| `/whitelist` | Moved | /setup server → Game commands (leave Whitelist add empty to turn requests off) |
| `/whitelist add` | Same name | /whitelist add |
| `/whitelist remove` | Same name | /whitelist remove |
| `/whitelist request_channel` | Moved | /setup bot → Channel for whitelist requests |
| `/whitelist wait_time` | Dropped | Requests wait for a moderator, or are approved at once by the auto-approve role. |
| `/whitelist auto` | Moved | /setup bot → Roles → Auto-approve whitelist requests |
| `/whitelist donator_bypass` | Dropped | Give donators the auto-approve role instead. |
| `/whitelist require_link` | Dropped | Auto-approval already needs a verified link; other requests go to a moderator. |
| `/whitelist_reply add` | Dropped | Portcullis's replies are fixed, plain-English messages. |
| `/whitelist_reply remove` | Dropped | Portcullis's replies are fixed, plain-English messages. |
| `/whitelist_reply list` | Dropped | Portcullis's replies are fixed, plain-English messages. |
| `/whitelist_request` | Moved | /whitelist request |
