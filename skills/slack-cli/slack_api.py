#!/usr/bin/env python3
"""Call ANY Slack Web API method using slackcli's stored browser tokens.

slackcli itself only does auth / conversations list+read / messages send. This
helper covers everything else (search.messages, reactions.add, users.info,
users.lookupByEmail, conversations.open|create|invite, chat.update, chat.delete,
pins.add, reminders, files, …) by calling the Web API directly with the tokens
slackcli already stored in ~/.config/slackcli/workspaces.json.

Key detail: slackcli stores `xoxd_token` URL-DECODED, but the Slack `Cookie: d=`
header wants it URL-ENCODED (browser form). This re-encodes it automatically.

Usage:
  slack_api.py <method> [--workspace ID|name] [key=value ...]
  slack_api.py <method> [--workspace ID|name] --json '<raw json body>'

Params are form-encoded by default; use --json for bodies with arrays/objects
(e.g. Block Kit). Prints the JSON response; exits non-zero if Slack returns
ok:false.

Examples:
  slack_api.py auth.test
  slack_api.py search.messages query="in:#general deploy" count=5
  slack_api.py users.lookupByEmail email=mike@example.com
  slack_api.py conversations.open users=U012ABC            # -> DM channel id
  slack_api.py reactions.add channel=C012 name=tada timestamp=1712345678.000200
  slack_api.py chat.update channel=C012 ts=1712345678.000200 text="edited"
  slack_api.py chat.delete channel=C012 ts=1712345678.000200
  slack_api.py --workspace PriceLove pins.add channel=C012 timestamp=1712345678.000200
  slack_api.py chat.postMessage --json '{"channel":"C012","blocks":[{"type":"section","text":{"type":"mrkdwn","text":"*hi*"}}]}'
"""
import json, os, sys, urllib.error, urllib.parse, urllib.request

CONFIG = os.path.expanduser("~/.config/slackcli/workspaces.json")


def load_ws(selector):
    with open(CONFIG) as f:
        cfg = json.load(f)
    workspaces = cfg.get("workspaces", {})
    if not workspaces:
        sys.exit(f"ERROR: no workspaces in {CONFIG}. Authenticate first (see the slack-login skill).")
    if not selector:
        selector = cfg.get("default_workspace")
    # match by exact team id, else case-insensitive name contains
    if selector in workspaces:
        return workspaces[selector]
    for ws in workspaces.values():
        if selector and selector.lower() in (ws.get("workspace_name", "").lower()):
            return ws
    known = ", ".join("{}({})".format(w.get("workspace_id"), w.get("workspace_name"))
                      for w in workspaces.values())
    sys.exit(f"ERROR: workspace {selector!r} not found. Known: {known}")


def main():
    argv = sys.argv[1:]
    if not argv:
        sys.exit(__doc__)
    method = None
    workspace = None
    raw_json = None
    params = {}
    i = 0
    while i < len(argv):
        a = argv[i]
        if a.startswith("--workspace="):
            workspace = a.split("=", 1)[1]; i += 1; continue
        if a.startswith("--json="):
            raw_json = a.split("=", 1)[1]; i += 1; continue
        if a == "--workspace":
            workspace = argv[i + 1]; i += 2; continue
        if a == "--json":
            raw_json = argv[i + 1]; i += 2; continue
        if a in ("-h", "--help"):
            sys.exit(__doc__)
        if "=" in a and not a.startswith("-"):
            k, v = a.split("=", 1); params[k] = v; i += 1; continue
        if method is None:
            method = a; i += 1; continue
        sys.exit(f"ERROR: unexpected arg {a!r}")
    if not method:
        sys.exit("ERROR: first positional arg must be a Slack API method (e.g. auth.test)")

    ws = load_ws(workspace)
    if not ws.get("xoxc_token") or not ws.get("xoxd_token"):
        sys.exit(f"ERROR: workspace {ws.get('workspace_id')} is missing browser tokens "
                 f"(auth_type={ws.get('auth_type')}); re-authenticate via the slack-login skill.")
    xoxc = ws["xoxc_token"]
    xoxd_encoded = urllib.parse.quote(ws["xoxd_token"], safe="")  # re-encode for Cookie header

    url = f"https://slack.com/api/{method}"
    headers = {"Authorization": f"Bearer {xoxc}", "Cookie": f"d={xoxd_encoded}"}
    if raw_json is not None:
        body = raw_json.encode()
        headers["Content-Type"] = "application/json; charset=utf-8"
    else:
        body = urllib.parse.urlencode(params).encode()
        headers["Content-Type"] = "application/x-www-form-urlencoded"

    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            resp = json.loads(r.read())
    except urllib.error.HTTPError as e:
        detail = e.read().decode(errors="replace")
        try:
            resp = json.loads(detail)
        except ValueError:
            resp = {"ok": False, "error": f"http_{e.code}", "body": detail[:500]}
        print(json.dumps(resp, indent=2))
        sys.exit(1)
    except urllib.error.URLError as e:
        print(json.dumps({"ok": False, "error": "network_error", "detail": str(e.reason)}))
        sys.exit(1)
    print(json.dumps(resp, indent=2))
    sys.exit(0 if resp.get("ok") else 1)


if __name__ == "__main__":
    main()
