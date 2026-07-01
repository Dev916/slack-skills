# slack-skills

Two complementary [Claude Code](https://claude.com/claude-code) skills for driving
Slack from the terminal via [`slackcli`](https://github.com/) and your own Slack
**browser session tokens** — so any agent can read channels, search, send, react,
and more, across multiple workspaces.

| Skill | Use it when |
|-------|-------------|
| **`slack-login`** | `slackcli` reports `invalid_auth`, or you're adding / re-authenticating a workspace. Extracts `xoxc` + `xoxd` browser tokens and registers them with `slackcli`. |
| **`slack-cli`** | Any day-to-day Slack operation: read/search/send/reply/react/edit, look up users, open DMs. Wraps `slackcli`'s native commands and adds a Slack **Web API escape hatch** for everything it doesn't implement. |

They're designed to work together: `slack-cli` points you at `slack-login` whenever
auth has expired.

## Why browser tokens?

Slack's managed connectors and bot tokens are single-account or require app
installation. These skills instead reuse the session you already have in a
browser — extracting the `xoxc` API token (from `localStorage`) and the httpOnly
`xoxd` `d` cookie (via the Chrome DevTools Protocol). That lets one agent act as
you across every workspace you're signed into, with no app to install.

The key insight encoded here: the `xoxd` value is an **httpOnly cookie**, so page
JavaScript can't read it — you need CDP (the token extraction uses a
CDP-exposing browser on `localhost:9222`). See `skills/slack-login/SKILL.md`.

## Install

Copy (or symlink) the skills into your Claude Code skills directory:

```sh
git clone https://github.com/Dev916/slack-skills.git
cp -R slack-skills/skills/slack-login ~/.claude/skills/
cp -R slack-skills/skills/slack-cli   ~/.claude/skills/
```

Other runtimes (Codex, Copilot CLI, Gemini CLI) also read `~/.agents/skills/`.

## Requirements

- [`slackcli`](https://github.com/) on your `PATH`
- `python3` (standard library only — no pip installs)
- A CDP-exposing Chrome for the initial token extraction (the
  `superpowers-chrome` browser used by Claude Code exposes CDP on `localhost:9222`)

## Quick start

```sh
# 1. Authenticate a workspace (see slack-login for the browser-token flow)
slackcli auth list

# 2. Read / list
slackcli conversations list --workspace <TEAM_ID>
slackcli conversations read <CHANNEL_ID> --json 2>/dev/null | jq .

# 3. Send / reply
slackcli messages send --recipient-id <CHANNEL_ID> --message "hi"

# 4. Anything slackcli doesn't do — Web API escape hatch:
python3 ~/.claude/skills/slack-cli/slack_api.py search.messages query="in:#general deploy" count=10
python3 ~/.claude/skills/slack-cli/slack_api.py reactions.add channel=C012 name=tada timestamp=<ts>
```

## Security

- **No secrets are stored in this repo.** The scripts read tokens at runtime from
  `slackcli`'s local store (`~/.config/slackcli/workspaces.json`).
- These tokens are *your* Slack session — treat them like a password. They live
  only on your machine.
- `slack_api.py` acts as the logged-in user. Sending, editing, and deleting are
  real actions in real workspaces — agents should confirm side-effecting calls.

## License

MIT — see [LICENSE](LICENSE).
