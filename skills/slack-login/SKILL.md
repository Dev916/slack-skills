---
name: slack-login
description: Use when slackcli reports invalid_auth, when adding or re-authenticating a Slack workspace for the CLI, or when tokens have expired and an agent needs Slack read/send access. Covers extracting browser session tokens (xoxc + xoxd) and registering them with slackcli.
---

# Slack Login (CLI via browser tokens)

## Overview

Authenticate `slackcli` to a workspace by extracting the two browser session
tokens — `xoxc` (API token, in localStorage) and `xoxd` (the httpOnly `d`
cookie) — and registering them with `slackcli auth login-browser`. After login,
use [[slack-send]] to read/send.

**Core insight — use the superpowers-chrome browser, NOT claude-in-chrome.**
The `xoxd` value is Slack's httpOnly `d` cookie: page JavaScript can't read it,
and claude-in-chrome's network log exposes no request headers, so claude-in-chrome
**cannot** get it. Only the Chrome DevTools Protocol can, and the
`superpowers-chrome` browser (the `use_browser` MCP tool) exposes CDP on
`localhost:9222`.

## Prerequisites

- `slackcli` installed (`which slackcli`).
- The `superpowers-chrome` browser running. Its MCP tool is
  **`mcp__plugin_superpowers-chrome_chrome__use_browser`** — the `{action, payload}`
  shorthand below maps to that tool's `action` and `payload` params. An `eval`
  action returns the expression's value as a **bare string** (e.g. `xoxc-…`, no
  surrounding quotes) — capture it verbatim.
- CDP is on `localhost:9222` once the browser is up: `curl -s localhost:9222/json/version`.
- The target workspace signed in inside that browser (see step 2).
- This skill's dir (holds `extract_xoxd.py`): `~/.claude/skills/slack-login`.

## Steps

**1. Confirm you need it.** `invalid_auth` means expired/missing:
```bash
slackcli auth list
slackcli conversations list --workspace=<TEAM_ID>   # invalid_auth => re-login
```

**2. Load the workspace in superpowers-chrome — ALWAYS navigate, never skip.**
```
use_browser {action:"navigate", payload:"https://app.slack.com/client/<TEAM_ID>"}
```
This is mandatory even for a live session: the localStorage reads below only work
on the `app.slack.com` origin, so the active tab MUST be a Slack tab. If a live
session exists this loads instantly (no login needed). If it shows a sign-in page,
ask the user to sign in — managed Workspace domains may show a "Your organization
will manage this profile" consent; the user must click through it, you cannot.
Continue once you see channels.

Don't know the TEAM_ID / domain / name? List what's signed in (also gives the
`--workspace-name` and `--workspace-url` values for step 5):
```
use_browser {action:"eval", payload:"JSON.stringify(Object.values(JSON.parse(localStorage.getItem('localConfig_v2')||'{}').teams||{}).map(t=>({name:t.name,id:t.id,domain:t.domain,url:t.url})))"}
```

**3. Get `xoxc` from localStorage** (per-workspace token). The eval returns the
bare `xoxc-…` string — capture it for step 5:
```
use_browser {action:"eval", payload:"(JSON.parse(localStorage.getItem('localConfig_v2')||'{}').teams?.['<TEAM_ID>']||{}).token || 'NOT_FOUND: wrong tab or unknown TEAM_ID'"}
```
If you get `NOT_FOUND`, the active tab isn't Slack or the TEAM_ID is wrong — redo step 2.

**4. Get `xoxd` via CDP** (httpOnly `d` cookie; domain-wide, same for every
workspace — extract once, reuse). Prints the decoded value to stdout; redirect it
to a temp file:
```bash
python3 ~/.claude/skills/slack-login/extract_xoxd.py --decoded > /tmp/xoxd.txt
```

**5. Register with slackcli** (paste the bare `xoxc-…` from step 3). slackcli
derives the team ID from the token and keys the workspace by it, so registering
by URL here makes `--workspace=<TEAM_ID>` work in step 6 (verified):
```bash
slackcli auth login-browser \
  --xoxc="<xoxc-… from step 3, no quotes around it in the eval output>" \
  --xoxd="$(cat /tmp/xoxd.txt)" \
  --workspace-url=https://<domain>.slack.com \
  --workspace-name="<Name>"          # optional/cosmetic
```

**6. Verify, then clean up:**
```bash
slackcli conversations list --workspace=<TEAM_ID>   # expect a channel list
rm -f /tmp/xoxd.txt
```

## Critical gotchas

| Gotcha | Rule |
|--------|------|
| claude-in-chrome can't read `d` cookie | Use superpowers-chrome + CDP (`localhost:9222`) only |
| `xoxd` encoding | **URL-DECODED** for `slackcli auth login-browser`; **URL-ENCODED** for curl `Cookie: d=` headers (don't decode `%2B` etc.) |
| `d` cookie scope | Domain-wide (`.slack.com`) — one value works for all workspaces in that browser profile |
| TEAM_ID + domain | From `localConfig_v2.teams`, or the `app.slack.com/client/<TEAM_ID>` URL |
| Re-auth of a live session | Browser still logged in → re-extract tokens, no user sign-in needed |
| Managed domains (custom-domain Google Workspace) | May require org-admin consent; the user handles any sign-in/consent screen |

## Set the default workspace (optional)
```bash
slackcli auth set-default <TEAM_ID>   # then --workspace can be omitted
```

## Common mistakes
- Reaching for claude-in-chrome — it will get `xoxc` but never `xoxd`. Dead end.
- Passing the URL-encoded `xoxd` to `slackcli` (login fails). Use `--decoded`.
- Assuming a fresh browser has the session — a new superpowers-chrome profile is
  logged out; the user must sign in once first.
