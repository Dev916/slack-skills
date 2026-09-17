---
name: slack-login
description: Use when slackcli reports invalid_auth, when adding or re-authenticating a Slack workspace for the CLI, or when tokens have expired and an agent needs Slack read/send access. Covers extracting browser session tokens (xoxc + xoxd) and registering them with slackcli — from the superpowers-chrome browser (main path) OR from a regular Chrome profile the workspace is already signed into (fallback).
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

**Which path?** If the workspace is (or can be) signed into the superpowers-chrome
browser, use the **main path** below. If the workspace is only signed into one of
the user's **regular Chrome profiles** (Profile 8, Profile 37, …) — the common case,
and what the user usually means by "log into my <X> Slack, it's open in my browser" —
jump to **[Fallback: regular Chrome profile](#fallback-workspace-signed-into-a-regular-chrome-profile)**.
That path is fully automated by `extract_from_profile.py` and needs no user sign-in.

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

## Fallback: workspace signed into a regular Chrome profile

Use this when the workspace is signed into an **ordinary Chrome profile**, not the
superpowers-chrome browser. You **cannot** CDP into a running Chrome profile that
wasn't launched with `--remote-debugging-port`, and you can't relaunch that profile
with one while Chrome already has it open. Reading the profile's files by hand is a
trap (see "Why the by-hand route fails" below).

Instead, `extract_from_profile.py` copies the profile's essentials into a throwaway
`--user-data-dir`, launches a **headless Chrome on that copy** with a debug port, and
lets Chrome itself decode localStorage and decrypt the `d` cookie. Both values come
out correct and provably matched (it runs `auth.test` before returning).

**One command** — auto-detects the profile, extracts, validates, and registers:
```bash
python3 ~/.claude/skills/slack-login/extract_from_profile.py \
  --team <TEAM_ID> --register --name "<Name>"
```
It prints `auth.test OK — team=… user=…` then the slackcli success line, and a final
JSON `{"ok": true, "registered": true, …}`. Secrets never pass through the agent's
context; the throwaway profile and any temp files are removed on exit.

Then verify as usual:
```bash
slackcli conversations list --workspace=<TEAM_ID>
```

**Options:**
- `--profile "Profile 37"` — force a profile dir (skip auto-detect).
- `--profile-name Clever` — match a profile by its display name substring.
- omit `--register` — extract only; writes `xoxc`/`xoxd` to `0600` temp files in
  `$TMPDIR` and prints their paths (the `xoxd` file is already URL-decoded for
  slackcli). Delete them yourself afterward.
- `--port 9333` (default), `--chrome <path>`, `--wait 18` (secs to wait for the token).

**Don't know the TEAM_ID?** Read the profile's display name from Chrome's
`Local State` (`~/Library/Application Support/Google/Chrome/Local State` →
`profile.info_cache`) to find the right profile, then open Slack there to read the
team id — or just run the script with `--profile-name <X>` and no team and it will
error with the team ids it found. Simplest: the user usually knows the workspace URL
or you can get the TEAM_ID from `app.slack.com/client/<TEAM_ID>`.

**No Keychain prompt.** Chrome is in the ACL for its own "Chrome Safe Storage"
Keychain item, so the headless Chrome decrypts the `d` cookie silently. (Decrypting
by hand with the `security` CLI *does* prompt, because that tool isn't in the ACL —
another reason to let Chrome do it.)

### Why the by-hand route fails (do NOT do this)
- **`xoxc` is Snappy-compressed in leveldb.** The profile's `Local Storage/leveldb`
  is Snappy-compressed, and a token can straddle a compression block boundary. Naive
  `strings`/`grep` returns a **truncated** token (looks like `xoxc-AAA-BBB`, missing
  the later groups + the 64-hex tail) → `invalid_auth`. Only a real leveldb/Snappy
  decoder (or Chrome itself) reconstructs it.
- **The `d` cookie is Keychain-encrypted AND URL-encoded.** Decrypting it yourself
  (AES-128-CBC, key = PBKDF2 of the "Chrome Safe Storage" Keychain secret) yields the
  **URL-encoded** cookie value; slackcli needs it **URL-decoded**. Passing the encoded
  form → `invalid_auth`. Letting Chrome read it via CDP `Network.getAllCookies` gives
  the raw encoded value, which the script then `unquote()`s for slackcli.

## Critical gotchas

| Gotcha | Rule |
|--------|------|
| claude-in-chrome can't read `d` cookie | Use superpowers-chrome + CDP (`localhost:9222`) only |
| `xoxd` encoding | **URL-DECODED** for `slackcli auth login-browser`; **URL-ENCODED** for curl `Cookie: d=` headers (don't decode `%2B` etc.) |
| `d` cookie scope | Domain-wide (`.slack.com`) — one value works for all workspaces in that browser profile |
| TEAM_ID + domain | From `localConfig_v2.teams`, or the `app.slack.com/client/<TEAM_ID>` URL |
| Re-auth of a live session | Browser still logged in → re-extract tokens, no user sign-in needed |
| Managed domains (custom-domain Google Workspace) | May require org-admin consent; the user handles any sign-in/consent screen |
| Workspace only in a regular Chrome profile | Don't sign in again in superpowers-chrome — use `extract_from_profile.py` (fallback section); it reuses the existing session, no user action |
| Reading a profile's files by hand | `xoxc` is Snappy-compressed (truncates) and the `d` cookie is Keychain-encrypted + URL-encoded — let headless Chrome + CDP decode both instead |

## Set the default workspace (optional)
```bash
slackcli auth set-default <TEAM_ID>   # then --workspace can be omitted
```

## Common mistakes
- Reaching for claude-in-chrome — it will get `xoxc` but never `xoxd`. Dead end.
- Passing the URL-encoded `xoxd` to `slackcli` (login fails). Use `--decoded`.
- Assuming a fresh browser has the session — a new superpowers-chrome profile is
  logged out; the user must sign in once first.
- Trying to CDP into the user's regular Chrome — it has no debug port, and you
  can't relaunch that profile with one while Chrome is running. Use the fallback
  (`extract_from_profile.py`), which works on a throwaway copy of the profile.
- Hand-parsing the profile's leveldb/cookies for tokens — truncated `xoxc` and
  wrongly-encoded `xoxd` both yield `invalid_auth`. Let Chrome decode them.
