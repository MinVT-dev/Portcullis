# Security checks

How the repository checks itself for vulnerable dependencies, leaked secrets and risky code, and what to do when a check fails. To report a vulnerability, see [SECURITY.md](../SECURITY.md).

## The checks

| Check | Tool | Runs on | Looks for |
|---|---|---|---|
| Dependency audit | `pip-audit` | Every push and pull request (`ci.yml`), plus Mondays (`security.yml`) | Installed packages with a published vulnerability |
| Secret scan | gitleaks | Pushes to `main`, pull requests, Mondays | Tokens, passwords and keys anywhere in the git history |
| Code scan | CodeQL | Pushes to `main`, pull requests, Mondays | Injection, unsafe file paths and similar bugs in the Python code and in the workflow files |
| Workflow lint | zizmor | Pushes to `main`, pull requests, Mondays | Unpinned actions, leaked credentials and template injection in `.github/workflows` |
| Dependency updates | Dependabot | Mondays | Newer Python packages and GitHub Actions, opened as pull requests |

The Monday runs matter even when nobody changes the code, because new vulnerabilities are published against old versions.

To run `security.yml` by hand: **Actions** tab, **Security checks**, **Run workflow**.

### Who gets told

- A failing check turns the commit or pull request red.
- When a **scheduled** run fails, GitHub emails the person who last changed the `cron:` line in that workflow.
- No check blocks a merge on its own. To make one required, add it to a branch rule for `main` (Settings, Rules).

## Reading the results

Open the run from the **Actions** tab. Each job writes a short report on the run page under **Summary**.

### Dependency audit fails

The summary lists each package, the installed version, the advisory id (`GHSA-...` or `PYSEC-...`) and the first fixed version.

1. Open the advisory and check whether Portcullis uses the affected feature. Many advisories are about a feature we never call.
2. Raise the lower bound in `pyproject.toml` (and in `packages/portcullis-api/pyproject.toml` if the package is listed there too), for example `"aiohttp>=<fixed version>,<4"`.
3. If no fixed version exists yet and we are not affected, add `--ignore-vuln <id>` to the `pip-audit` lines in both workflows, with a comment saying why and when to look again.

A Dependabot pull request for the same package usually follows. It waits seven days after a release on purpose, so a hijacked release is usually pulled before we see it.

### Secret scan fails

The log shows the rule, the file, the commit and the line. The secret itself is replaced with `REDACTED`.

- **A real secret:** treat it as stolen. Rotate it first (reset the Discord bot token, change the AMP password), then remove it from the code. Rewriting history does not undo the leak, because anyone who cloned the repository already has it.
- **A false alarm**, such as a made-up token in a test: add a narrow entry to `.gitleaks.toml` with a `description` saying why. Allow one rule in one path, never a whole folder for every rule. To skip a single line instead, end it with the comment `# gitleaks:allow`.

### CodeQL fails

Each finding shows as a warning on the line it points at, and the summary lists them all. The raw results are kept as the `codeql-python` and `codeql-actions` artifacts for 30 days.

- Fix the code if the finding is real.
- If it is a false alarm, say why in the pull request. Generated code (`portcullis_api/models.py`, `portcullis_api/api/`, the spec) is skipped in `.github/codeql/codeql-config.yml`, because `generate_client --check` covers it.

While the repository is private, findings appear only in the run. Once it is public, the workflow also sends them to the **Security** tab, where they can be dismissed with a reason.

### Workflow lint fails

zizmor names the problem and the line, with a link to an explanation. The usual one is an action used by tag (`@v4`) instead of by commit. Pin it with the full commit hash and keep the tag as a comment:

```yaml
- uses: actions/checkout@3d3c42e5aac5ba805825da76410c181273ba90b1  # v7.0.1
```

Dependabot keeps these hashes up to date.

## Settings to switch on

These are repository settings. The workflows can't turn them on.

**Settings, Advanced Security** (called "Code security" on some accounts):

| Setting | While private | Once public |
|---|---|---|
| Dependency graph | Turn on (alerts need it) | On by default |
| Dependabot alerts | Turn on | Turn on |
| Dependabot security updates | Turn on | Turn on |
| Private vulnerability reporting | Not offered | Turn on |
| Secret scanning and push protection | Paid add-on | Turn on (free) |
| CodeQL **default setup** | Paid add-on | Leave **off**: `security.yml` already runs CodeQL, and GitHub rejects workflow results while default setup is on |

Push protection refuses a push that contains a known token format before it reaches GitHub, which beats finding it afterwards.

## What these checks do not cover

- A secret that never reaches git, such as one pasted into Discord or a chat. The bot's own redaction covers its logs and replies.
- A vulnerability nobody has published yet. Dependencies are compared with published advisories, not read.
- How your AMP panel or Discord server is set up. See [Safety and privacy](../README.md#safety-and-privacy) in the README.
