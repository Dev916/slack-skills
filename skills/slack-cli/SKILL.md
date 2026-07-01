---
name: slack-cli
description: Use when reading Slack channels or DMs, sending or replying to Slack messages, searching Slack, adding reactions, looking up users, editing/deleting messages, or performing any Slack operation from the terminal. Covers the slackcli commands plus a Slack Web API escape hatch for operations slackcli itself does not implement.
---

# Slack CLI

## Overview

`slackcli` natively does three things: **auth**, **read conversations**, **send
messages**. For everything else (search, reactions, user/DM lookup, edits,
deletes, pins, files) use the **Web API escape hatch** `slack_api.py`, which
reuses the tokens slackcli already stored. Not authenticated / `invalid_auth`?
→ [[slack-login]].

## Workspace targeting

- Every command accepts `--workspace <id|name>`; omit it to use the default.
- `slackcli auth list` — list workspaces & the default.
- `slackcli auth set-default <TEAM_ID>` — change default.

## Native slackcli commands (v0.1.x)

| Task | Command |
|------|---------|
| List channels/DMs (to get IDs) | `slackcli conversations list [--types public_channel,private_channel,mpim,im] [--exclude-archived] [--limit N] [--workspace W]` |
| Read history | `slackcli conversations read <channel-id> [--limit N] [--oldest TS] [--latest TS] [--exclude-replies] [--json] [--workspace W]` |
| Read a specific thread | `slackcli conversations read <channel-id> --thread-ts <ts>` |
| Send a message | `slackcli messages send --recipient-id <C…|U…> --message "text" [--workspace W]` |
| Reply in a thread | `slackcli messages send --recipient-id <C…> --message "text" --thread-ts <ts>` |
| DM a user | `slackcli messages send --recipient-id <U…> --message "text"` (use the **user** id) |

- **`--json`** on `read` includes message timestamps — you need those `ts` values
  for thread replies, reactions, edits, pins. Progress text goes to **stderr**, so
  stdout is clean JSON: `slackcli conversations read <ch> --json 2>/dev/null | jq …`.
- **Channel ID prefixes:** `C…` channel, `D…` DM, `G…`/mpim group. Get them from
  `conversations list` (or `conversations.open` for a DM — see below).

## Everything else — Web API escape hatch

```bash
python3 ~/.claude/skills/slack-cli/slack_api.py <method> [--workspace W] [key=value …] [--json '<body>']
```
Reads tokens from slackcli's store, re-encodes the `d` cookie, POSTs to
`https://slack.com/api/<method>`, prints JSON, exits non-zero on `ok:false`.
**Any** [Slack Web API method](https://api.slack.com/methods) works:

| Op | Call |
|----|------|
| Search messages | `slack_api.py search.messages query="in:#general deploy" count=20` |
| User by email | `slack_api.py users.lookupByEmail email=foo@bar.com` |
| User info / list | `slack_api.py users.info user=U012ABC` · `slack_api.py users.list limit=200` |
| Open a DM (get `D…` id) | `slack_api.py conversations.open users=U012ABC` |
| React | `slack_api.py reactions.add channel=C012 name=tada timestamp=<ts>` |
| Edit a message | `slack_api.py chat.update channel=C012 ts=<ts> text="new"` |
| Delete a message | `slack_api.py chat.delete channel=C012 ts=<ts>` |
| Pin | `slack_api.py pins.add channel=C012 timestamp=<ts>` |
| Rich (Block Kit) post | `slack_api.py chat.postMessage --json '{"channel":"C012","blocks":[…]}'` |

Params are form-encoded; use `--json` for bodies containing arrays/objects.

## Auth & tokens

- Tokens: `~/.config/slackcli/workspaces.json` — per-workspace `xoxc_token` /
  `xoxd_token` (stored **URL-decoded**) plus `default_workspace`. `slack_api.py`
  reads these directly.
- `invalid_auth` anywhere → re-authenticate via [[slack-login]].

## Gotchas

| Symptom | Fix |
|---------|-----|
| `invalid_auth` | Tokens expired → [[slack-login]] |
| `channel_not_found` on a DM via `slackcli` | Use the **user** id (`U…`) with `messages send`, or `conversations.open users=U…` to get the `D…` id |
| Need a message's `ts` | `slackcli conversations read <ch> --json` |
| Posts look "bot-ish" | Expected — browser tokens act as the logged-in user |
| Hand-written curl `Cookie: d=` | xoxd must be **URL-ENCODED** there (store keeps it decoded) — or just use `slack_api.py`, which encodes it |

## Common mistakes

- Using a `D…` id with `messages send` when it errors — pass the `U…` user id instead.
- Forgetting `--workspace` when not on the default workspace.
- Feeding decoded xoxd into a raw curl Cookie header. Let `slack_api.py` handle it.
