# Security policy

Portcullis talks to your AMP panel with a real login and to Discord with a bot token, so a bug here can hand someone control of your game servers. If you find one, please tell us privately first.

## Reporting a vulnerability

**Email [support@solvra-panel.com](mailto:support@solvra-panel.com)** with `[Portcullis security]` at the start of the subject line.

Please don't open a public issue, pull request or Discord post about it until a fix is out.

When the repository is public, GitHub's **Report a vulnerability** button on the [Security tab](https://github.com/MinVT-dev/Portcullis/security) will also work. Either route is fine.

## What to include

- The version: a release number, or the commit you ran (`git rev-parse HEAD`).
- Which part is affected: the bot, the `portcullis-api` package, a tool in `tools/`, or a CI workflow.
- The steps to reproduce it, and what an attacker gains (for example: runs console commands, sees another server, reads a password).
- The Discord permissions and roles involved, if the problem is about who can do what.
- Logs or screenshots, **with secrets removed**.

## Never send secrets

Don't paste any of these into a report, an issue, a log or a screenshot:

- your Discord bot token;
- AMP passwords, login tokens (JWTs) or hub tickets;
- AMP licence keys;
- Discord invite links to a private server;
- the contents of your `.env` file or the bot's database.

Replace them with `[REDACTED]`. We never need a working secret to reproduce a bug.

If a secret was exposed, rotate it before you write to us: reset the bot token in the Discord developer portal, and change the AMP bot user's password in the panel.

## Supported versions

Portcullis is pre-release. Only the latest code gets fixes.

| Component | Supported |
|---|---|
| `portcullis` (the bot): the `main` branch and the newest release | Yes |
| `portcullis-api`: the newest 0.x release | Yes |
| Older commits and releases | No, please update first |
| Gatekeeper V2 and V3 (separate projects) | No |

## What happens next

This is a volunteer project, so these are targets, not guarantees:

- We confirm we got your report within **7 days**.
- We tell you whether we can reproduce it, and update you at least every **14 days** until it is closed.
- When a fix ships, the release notes describe the problem. We credit you there unless you ask us not to.

Please give us a chance to ship a fix before you publish details. If you have a disclosure date in mind, say so in your first message.

## Scope

In scope: the code in this repository, including the bot, `portcullis-api`, `tools/` and the GitHub Actions workflows.

Not in scope, please report these to their owners instead:

- AMP itself: CubeCoders.
- Discord, or discord.py.
- A game server or game template.
- A vulnerability in a dependency. Report it upstream, but do tell us if Portcullis uses the affected code in a way that exposes you.

Test only against an AMP panel and a Discord server that you own.

## How the repository is checked

Dependencies, secrets, code and workflows are scanned every week and on every pull request. [docs/SECURITY_CHECKS.md](docs/SECURITY_CHECKS.md) explains each check.
