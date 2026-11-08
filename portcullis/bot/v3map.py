# SPDX-License-Identifier: AGPL-3.0-or-later
"""Every Gatekeeper V3 slash command and where it lives in Portcullis.

The one source for ``/help v3:`` and ``docs/V3_PARITY.md`` (``render_markdown`` writes the doc; a test
checks the committed file matches). Counted from GatekeeperV3-Beta at commit ``760b1e7`` (4.8.0):
117 commands, the same list as DESIGN.md Appendix A.

Statuses:

- ``same``: a Portcullis command with the same name.
- ``moved``: the job is done elsewhere: another command, an option, a button or a ``/setup`` panel field.
- ``later``: not built yet because the AMP 3 data it needs hasn't been seen working on a real panel.
- ``dropped``: not carried over, with the reason.
"""

from __future__ import annotations

from dataclasses import dataclass

STATUSES = ("same", "moved", "later", "dropped")
STATUS_LABELS = {"same": "Same name", "moved": "Moved", "later": "Not yet", "dropped": "Dropped"}

PERMS = "/setup bot → Who can do what (owners)"
BOARD = "/setup bot → the live server board"
CONSOLE_RULES = "/setup server → Console rules"
SCHEDULE = "/schedule list"
WL_RULES = "/setup bot → Whitelist rules"
DEV_MODE = "Discord's developer mode already shows role, channel and user ids (right-click → Copy ID)."


@dataclass(frozen=True)
class V3Command:
    v3: str
    status: str
    home: str  # the Portcullis command, button or panel field; for "dropped" and "later", the reason


def _e(v3: str, status: str, home: str) -> V3Command:
    return V3Command(v3, status, home)


V3_COMMANDS: tuple[V3Command, ...] = (
    # /bot
    _e("/bot donator", "moved", "/user → Toggle donator (also on right-click → Apps → Portcullis user)"),
    _e("/bot moderator", "moved", f"Operator roles in .env (PORTCULLIS_OPERATOR_ROLE_IDS), plus {PERMS}"),
    _e("/bot permissions", "moved", f"{PERMS}, and /setup server → Server operators (owners)"),
    _e("/bot settings", "moved", "/setup bot"),
    _e("/bot utils message_timeout", "dropped",
       "Portcullis's replies are private to the member who asked, so there is nothing to clear up later."),
    _e("/bot utils clear", "dropped", "Discord's own moderation tools do this, and a purge command is a risk."),
    _e("/bot utils roleid", "dropped", DEV_MODE),
    _e("/bot utils channelid", "dropped", DEV_MODE),
    _e("/bot utils userid", "dropped", DEV_MODE),
    _e("/bot utils steamid", "moved", "/player id (Steam ID formats)"),
    _e("/bot utils uuid", "moved", "/player id (Minecraft UUID lookup)"),
    _e("/bot utils ping", "moved", "/admin bot action:Status (owners)"),
    _e("/bot utils disconnect", "moved", "/admin bot action:Stop (owners)"),
    _e("/bot utils restart", "moved", "/admin bot action:Stop (owners); your service manager starts it again"),
    _e("/bot utils status", "moved", "/admin bot action:Status (owners)"),
    _e("/bot utils sync", "moved", "/admin bot action:Sync (owners)"),
    _e("/bot cog load", "dropped", "Portcullis has no plug-in cogs. Restart it to load new code."),
    _e("/bot cog unload", "dropped", "Portcullis has no plug-in cogs. Restart it to load new code."),
    _e("/bot cog reload", "dropped", "Portcullis has no plug-in cogs. Restart it to load new code."),
    # /server
    _e("/server start", "moved", "/server power action:Start, or the Start button on /server status"),
    _e("/server stop", "moved", "/server power action:Stop, or the Stop button on /server status"),
    _e("/server restart", "moved", "/server power action:Restart (or Restart with a warning in game chat)"),
    _e("/server kill", "later", "AMP 3.0.0.6 has a kill permission but no API route for it yet."),
    _e("/server msg", "moved", "/server send as:In-game chat"),
    _e("/server broadcast", "moved", "/server send as:In-game chat"),
    _e("/server status", "same", "/server status"),
    _e("/server users", "moved", "/server status (Online now), or /server list players:True"),
    _e("/server update", "dropped",
       "Portcullis re-reads AMP's server list every 5 minutes. "
       "For a game update, use /server power action:Update the game."),
    _e("/server backup", "moved", "/backups"),
    _e("/server settings info", "moved", "/setup server"),
    _e("/server settings avatar", "dropped", "Server art comes from AMP's template and the game registry."),
    _e("/server settings displayname", "moved", "/setup server → Name and visibility"),
    _e("/server settings host", "moved", "/setup server → Options → Address players connect to"),
    _e("/server settings donator", "moved",
       "/setup server → Name and visibility → Whitelist requests from donators only"),
    _e("/server settings role", "moved", "/setup server → Server operators (owners)"),
    _e("/server settings prefix", "moved", "/setup server → Name and visibility → Chat prefix"),
    _e("/server settings hidden", "moved", "/setup server → Name and visibility → Hide from members"),
    _e("/server console channel", "moved", "/setup relay kind:Console, or the channel pickers on /setup server"),
    _e("/server console denylist", "moved", f"{CONSOLE_RULES} → Blocked console commands"),
    _e("/server console rate_limit", "dropped",
       "Relays batch lines and drop the oldest when a channel can't keep up, so there is nothing to tune."),
    _e("/server console filter", "moved", f"{CONSOLE_RULES} → Hidden console lines"),
    _e("/server chat channel", "moved", "/setup relay kind:Chat"),
    _e("/server event channel", "moved", "/setup relay kind:Events"),
    _e("/server regex add", "moved", f"{CONSOLE_RULES} → Hidden console lines (start a line with re: for a pattern)"),
    _e("/server regex delete", "moved", f"{CONSOLE_RULES} → Hidden console lines"),
    _e("/server regex list", "moved", f"{CONSOLE_RULES} (the panel shows the current rules)"),
    _e("/regex_pattern add", "moved", f"{CONSOLE_RULES} → Hidden console lines"),
    _e("/regex_pattern delete", "moved", f"{CONSOLE_RULES} → Hidden console lines"),
    _e("/regex_pattern update", "moved", f"{CONSOLE_RULES} → Hidden console lines"),
    _e("/regex_pattern list", "moved", f"{CONSOLE_RULES} (the panel shows the current rules)"),
    # /dbserver
    _e("/dbserver cleanup", "dropped",
       "Portcullis syncs its server list with AMP on its own and marks servers that are gone."),
    _e("/dbserver change_instance_id", "dropped", "AMP 3 shard ids never change."),
    # /user and /role
    _e("/user info", "same", "/user"),
    _e("/user add", "moved", "/user (members are added the first time they use Portcullis)"),
    _e("/user update", "moved", "/user → its buttons (donator, blocked from requests, links)"),
    _e("/role", "moved", PERMS),
    # /alert
    _e("/alert create", "same", "/alert create"),
    _e("/alert list", "same", "/alert list"),
    _e("/alert delete", "moved", "Delete button on /alert list"),
    _e("/alert mute", "moved", "Mute button on /alert list"),
    _e("/alert channel", "moved", "/setup bot → Alert and log channels"),
    # /audit
    _e("/audit log", "moved", "/audit"),
    _e("/audit search", "moved", "/audit with its member, server, action and result filters"),
    _e("/audit stats", "moved", "/audit stats:True"),
    _e("/audit export", "moved", "/audit export:True"),
    _e("/audit channel", "moved", "/setup bot → Alert and log channels"),
    # /backup
    _e("/backup list", "moved", "/backups"),
    _e("/backup create", "moved", "Make a backup button on /backups"),
    _e("/backup delete", "moved", "Delete button on /backups"),
    # banners
    _e("/bannergroup create_group", "moved", f"{BOARD} (one self-updating message for every server)"),
    _e("/bannergroup info", "moved", BOARD),
    _e("/bannergroup rename", "dropped", "There is one board, so it needs no name."),
    _e("/bannergroup add", "moved", f"{BOARD} (it lists every server that isn't hidden)"),
    _e("/bannergroup remove", "moved", "/setup server → Name and visibility → Hide from members"),
    _e("/bannergroup delete_group", "moved", "/setup bot → Turn the server board off"),
    _e("/banner background", "moved", "/server banner (a status card per server; custom backgrounds aren't supported)"),
    _e("/banner settings", "moved", "/server banner"),
    _e("/banner_settings auto_update", "moved", f"{BOARD} (it updates itself)"),
    _e("/banner_settings type", "moved", "/setup bot → board style (embed or image)"),
    _e("/banner_settings auto_remove", "dropped",
       "The board edits one message in place, so there is nothing to remove."),
    _e("/banner_settings timeformat", "dropped", "Discord shows every time in each reader's own format."),
    _e("/banner_settings timezone", "dropped", "Discord shows every time in each reader's own time zone."),
    # config, diagnostics, help
    _e("/config export", "moved", "/admin export"),
    _e("/config import", "moved", "/admin import"),
    _e("/diagnostics", "moved", "/admin diagnostics"),
    _e("/help", "same", "/help"),
    _e("/about", "moved", "/help"),
    # /link
    _e("/link start", "moved", "/link server:<server> name:<your name>"),
    _e("/link status", "moved", "/link (with no options it shows your links)"),
    _e("/link clear", "moved", "/link → Remove my links"),
    # /player
    _e("/player channel", "moved", "/setup relay kind:Events"),
    _e("/player online", "moved", "/server list players:True"),
    _e("/player lookup", "same", "/player lookup"),
    _e("/player top", "same", "/player top"),
    # /poll
    _e("/poll create", "same", "/poll create"),
    _e("/poll list", "same", "/poll list"),
    _e("/poll close", "moved", "Close poll button on the poll"),
    _e("/poll results", "moved", "The poll message shows live counts"),
    _e("/poll delete", "dropped", "Close the poll, then use Discord's own Delete Message if you want it gone."),
    # /schedule
    _e("/schedule create", "later",
       "AMP 3's schedule step and trigger formats haven't been seen on a real panel yet. Create it in AMP for now."),
    _e("/schedule list", "same", SCHEDULE),
    _e("/schedule delete", "moved", f"Delete button on {SCHEDULE}"),
    _e("/schedule pause", "moved", f"Off button on {SCHEDULE}"),
    _e("/schedule resume", "moved", f"On button on {SCHEDULE}"),
    _e("/schedule run", "moved", f"Run now button on {SCHEDULE}"),
    # /whitelist
    _e("/whitelist", "moved", "/setup server → Game commands (leave Whitelist add empty to turn requests off)"),
    _e("/whitelist add", "same", "/whitelist add"),
    _e("/whitelist remove", "same", "/whitelist remove"),
    _e("/whitelist request_channel", "moved", "/setup bot → Channel for whitelist requests"),
    _e("/whitelist wait_time", "dropped",
       "Requests wait for a moderator, or are approved at once by the auto-approve role."),
    _e("/whitelist auto", "moved", "/setup bot → Roles → Auto-approve whitelist requests"),
    _e("/whitelist donator_bypass", "moved", f"{WL_RULES} → Donators skip the moderators"),
    _e("/whitelist require_link", "moved", f"{WL_RULES} → Only names the member has linked"),
    _e("/whitelist_reply add", "moved", f"{WL_RULES} → Added to the approval message (one message, not a list)"),
    _e("/whitelist_reply remove", "moved", f"{WL_RULES} → Added to the approval message (leave it empty)"),
    _e("/whitelist_reply list", "moved", f"{WL_RULES} → shown on the /setup bot panel"),
    _e("/whitelist_request", "moved", "/whitelist request"),
)

_BY_NAME = {c.v3: c for c in V3_COMMANDS}


def lookup(name: str) -> V3Command | None:
    """Find a V3 command by name, forgiving a missing slash, extra spaces and case."""
    key = "/" + " ".join(name.strip().lstrip("/").lower().split())
    return _BY_NAME.get(key)


def search(current: str, limit: int = 25) -> list[str]:
    needle = " ".join(current.strip().lstrip("/").lower().split())
    return [c.v3 for c in V3_COMMANDS if needle in c.v3.lower()][:limit]


def counts() -> dict[str, int]:
    out = dict.fromkeys(STATUSES, 0)
    for c in V3_COMMANDS:
        out[c.status] += 1
    return out


def _cell(text: str) -> str:
    return text.replace("|", "\\|")


def render_markdown() -> str:
    n = counts()
    lines = [
        "# Gatekeeper V3 commands in Portcullis",
        "",
        "<!-- Generated from portcullis/bot/v3map.py by render_markdown(). Edit that file, not this one. -->",
        "",
        f"Gatekeeper V3 had {len(V3_COMMANDS)} slash commands. This table says where each one went. "
        "In Discord, `/help v3:<old command>` gives the same answer.",
        "",
        "Portcullis folds them into far fewer commands on purpose: settings live in `/setup` panels, actions "
        "on a thing are buttons on that thing, and choices are options. The jobs are kept; the command names "
        "mostly aren't.",
        "",
        f"- **Same name:** {n['same']}",
        f"- **Moved** (another command, option, button or panel field): {n['moved']}",
        f"- **Not yet** (waiting on AMP 3 data seen on a real panel): {n['later']}",
        f"- **Dropped** (with the reason): {n['dropped']}",
        "",
        "| V3 command | Status | In Portcullis |",
        "|---|---|---|",
    ]
    for c in V3_COMMANDS:
        lines.append(f"| `{c.v3}` | {STATUS_LABELS[c.status]} | {_cell(c.home)} |")
    return "\n".join(lines) + "\n"
