#!/usr/bin/env python3
"""Log slackcli into a Slack workspace that is signed into a REGULAR Chrome
profile (not the superpowers-chrome browser).

Why this exists
---------------
The main path (see SKILL.md) reads tokens from the superpowers-chrome browser
over CDP on :9222. But your real workspaces are usually signed into ordinary
Chrome profiles (Profile 8, Profile 37, …) that expose NO debug port. You cannot
CDP into a running Chrome profile that wasn't started with --remote-debugging-port,
and you can't relaunch that profile with one while Chrome is already running it.

Reading the on-disk data by hand is a trap:
  * `xoxc` lives in the profile's Local Storage leveldb, which is Snappy-compressed.
    A token can straddle a compression block boundary, so naive `strings`/regex
    reads return a TRUNCATED token (looks like `xoxc-AAA-BBB` missing later groups).
  * the `d` cookie is AES-encrypted with the "Chrome Safe Storage" Keychain key,
    and its decrypted value is URL-ENCODED — slackcli wants it URL-DECODED.

This script sidesteps all of that: it copies the profile's essentials into a
throwaway user-data-dir, launches a headless Chrome on that copy WITH a debug
port, and lets Chrome itself decode localStorage + cookies. Chrome decrypts the
`d` cookie with the same Keychain key (one allow prompt may appear the first time),
so both values come out correct and provably matched (verified with auth.test).

Usage
-----
  # auto-detect which profile holds the workspace, extract, validate, and register:
  python3 extract_from_profile.py --team T52A6J85B --register --name "Clever"

  # just extract to temp files (prints paths + auth.test result, no registration):
  python3 extract_from_profile.py --team T52A6J85B

  # force a specific profile dir or match by display name:
  python3 extract_from_profile.py --team T52A6J85B --profile "Profile 37"
  python3 extract_from_profile.py --team T52A6J85B --profile-name Clever

Options: --port (default 9333), --chrome <path>, --wait <secs, default 18>.
Exit 0 on success; non-zero with a diagnostic otherwise. Secrets are written to
0600 temp files and (with --register) deleted after login.
"""
import argparse, glob, json, os, re, shutil, subprocess, sys, tempfile, time
import urllib.parse, urllib.request

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from extract_xoxd import ws_handshake, ws_send, ws_recv_response  # noqa: E402

CHROME_ROOT = os.path.expanduser("~/Library/Application Support/Google/Chrome")
CHROME_BINS = [
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    "/Applications/Google Chrome Beta.app/Contents/MacOS/Google Chrome Beta",
    os.path.expanduser("~/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"),
]


def find_chrome(explicit):
    for c in ([explicit] if explicit else []) + CHROME_BINS:
        if c and os.path.exists(c):
            return c
    sys.exit("ERROR: Google Chrome binary not found; pass --chrome <path>.")


def list_profiles():
    """Return {dir_name: display_name} from Local State, plus any bare Profile dirs."""
    out = {}
    try:
        ls = json.load(open(os.path.join(CHROME_ROOT, "Local State")))
        for d, meta in ls.get("profile", {}).get("info_cache", {}).items():
            out[d] = meta.get("name") or d
    except Exception:
        pass
    for p in glob.glob(os.path.join(CHROME_ROOT, "Profile *")) + [os.path.join(CHROME_ROOT, "Default")]:
        d = os.path.basename(p)
        out.setdefault(d, d)
    return out


def profile_has_team(profile_dir, team_id):
    """True if this profile's Local Storage leveldb mentions the team id (Snappy-
    safe: we only need the plaintext team id, which is short and appears literally)."""
    ldb = os.path.join(CHROME_ROOT, profile_dir, "Local Storage", "leveldb")
    if not os.path.isdir(ldb):
        return False
    needle = team_id.encode()
    for f in glob.glob(os.path.join(ldb, "*.ldb")) + glob.glob(os.path.join(ldb, "*.log")):
        try:
            if needle in open(f, "rb").read():
                return True
        except OSError:
            continue
    return False


def resolve_profile(args):
    profiles = list_profiles()
    if args.profile:
        if args.profile not in profiles and not os.path.isdir(os.path.join(CHROME_ROOT, args.profile)):
            sys.exit(f"ERROR: profile dir {args.profile!r} not found under {CHROME_ROOT}")
        return args.profile
    if args.profile_name:
        hits = [d for d, n in profiles.items() if args.profile_name.lower() in n.lower()]
        if not hits:
            sys.exit(f"ERROR: no profile whose name contains {args.profile_name!r}. "
                     f"Known: {sorted(profiles.values())}")
        # prefer one that actually has the team
        for d in hits:
            if profile_has_team(d, args.team):
                return d
        return hits[0]
    # auto-detect by scanning for the team id (newest-modified first)
    cands = sorted(profiles, key=lambda d: os.path.getmtime(os.path.join(CHROME_ROOT, d))
                   if os.path.isdir(os.path.join(CHROME_ROOT, d)) else 0, reverse=True)
    for d in cands:
        if profile_has_team(d, args.team):
            return d
    sys.exit(f"ERROR: no Chrome profile has {args.team} in localStorage. Is it signed "
             f"in? Pass --profile 'Profile NN' to force one. Known: {sorted(profiles.values())}")


def build_udd(profile_dir):
    udd = tempfile.mkdtemp(prefix="slack_udd_")
    dst = os.path.join(udd, "Default")
    os.makedirs(dst, exist_ok=True)
    src = os.path.join(CHROME_ROOT, profile_dir)
    shutil.copy2(os.path.join(CHROME_ROOT, "Local State"), os.path.join(udd, "Local State"))
    for name in ("Preferences", "Cookies"):
        p = os.path.join(src, name)
        if os.path.exists(p):
            shutil.copy2(p, os.path.join(dst, name))
    if os.path.isdir(os.path.join(src, "Local Storage")):
        shutil.copytree(os.path.join(src, "Local Storage"), os.path.join(dst, "Local Storage"))
    # newer Chrome keeps cookies under Network/Cookies
    net = os.path.join(src, "Network", "Cookies")
    if os.path.exists(net):
        os.makedirs(os.path.join(dst, "Network"), exist_ok=True)
        shutil.copy2(net, os.path.join(dst, "Network", "Cookies"))
    # collapse Local State to a single Default profile so Chrome opens it cleanly
    try:
        p = os.path.join(udd, "Local State")
        d = json.load(open(p))
        d.setdefault("profile", {})["info_cache"] = {"Default": {"name": "slack-extract"}}
        d["profile"]["last_used"] = "Default"
        d["profile"]["profiles_order"] = ["Default"]
        json.dump(d, open(p, "w"))
    except Exception:
        pass
    return udd


def cdp(port, payload_url, team, wait):
    """Launch nothing; assumes Chrome already up on `port`. Returns (token, d_value_encoded)."""
    tgt = json.load(urllib.request.urlopen(
        urllib.request.Request(f"http://localhost:{port}/json/new?{urllib.parse.quote(payload_url)}",
                               method="PUT"), timeout=10))
    ws = tgt["webSocketDebuggerUrl"]
    path = ws.split(f"localhost:{port}")[1]
    s = ws_handshake("localhost", port, path)
    mid = 0

    def call(method, params=None):
        nonlocal mid
        mid += 1
        ws_send(s, json.dumps({"id": mid, "method": method, "params": params or {}}))
        # generous tries: navigating Slack emits a flood of async events we must skip
        return ws_recv_response(s, mid, tries=600)

    # NOTE: do NOT enable Page/Network domains — that unleashes an event storm that
    # can bury command replies. Runtime.evaluate and Page.navigate work without them.
    call("Page.navigate", {"url": payload_url})

    tok_expr = ("(JSON.parse(localStorage.getItem('localConfig_v2')||'{}')"
                ".teams?.['%s']||{}).token || 'NONE'" % team)
    token = "NONE"
    deadline = time.time() + wait
    while time.time() < deadline:
        time.sleep(1.5)
        r = call("Runtime.evaluate", {"expression": tok_expr, "returnByValue": True})
        token = r.get("result", {}).get("result", {}).get("value", "NONE")
        if token and token != "NONE":
            break
    dval = None
    rc = call("Network.getAllCookies")
    for c in rc.get("result", {}).get("cookies", []):
        if c["name"] == "d" and "slack.com" in c.get("domain", ""):
            dval = c["value"]
    # workspace url for slackcli
    url_expr = ("(JSON.parse(localStorage.getItem('localConfig_v2')||'{}')"
                ".teams?.['%s']||{}).url || ''" % team)
    ru = call("Runtime.evaluate", {"expression": url_expr, "returnByValue": True})
    wurl = ru.get("result", {}).get("result", {}).get("value", "") or ""
    s.close()
    return token, dval, wurl


def auth_test(token, d_encoded):
    body = b""
    req = urllib.request.Request(
        "https://slack.com/api/auth.test", data=body, method="POST",
        headers={"Authorization": f"Bearer {token}",
                 "Cookie": f"d={d_encoded}",
                 "Content-Type": "application/x-www-form-urlencoded"})
    return json.loads(urllib.request.urlopen(req, timeout=15).read())


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--team", required=True, help="TEAM_ID, e.g. T52A6J85B")
    ap.add_argument("--profile", help="force a profile dir, e.g. 'Profile 37'")
    ap.add_argument("--profile-name", help="match a profile by display name substring")
    ap.add_argument("--register", action="store_true",
                    help="run slackcli auth login-browser with the extracted values")
    ap.add_argument("--name", help="workspace display name for --register (cosmetic)")
    ap.add_argument("--port", type=int, default=9333)
    ap.add_argument("--wait", type=int, default=18, help="max secs to wait for token")
    ap.add_argument("--chrome", help="path to the Chrome binary")
    args = ap.parse_args()

    chrome = find_chrome(args.chrome)
    profile = resolve_profile(args)
    print(f"[*] using profile: {profile}", file=sys.stderr)
    udd = build_udd(profile)
    proc = None
    try:
        proc = subprocess.Popen(
            [chrome, f"--user-data-dir={udd}", "--profile-directory=Default",
             f"--remote-debugging-port={args.port}", "--headless=new",
             "--no-first-run", "--no-default-browser-check", "--disable-sync",
             "--disable-extensions", "--disable-background-networking", "about:blank"],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        # wait for CDP
        for _ in range(20):
            try:
                urllib.request.urlopen(f"http://localhost:{args.port}/json/version", timeout=2)
                break
            except Exception:
                time.sleep(0.5)
        else:
            sys.exit(f"ERROR: headless Chrome CDP never came up on :{args.port}")

        token, dval, wurl = cdp(args.port, f"https://app.slack.com/client/{args.team}",
                                args.team, args.wait)
        if not token or token == "NONE":
            sys.exit(f"ERROR: no xoxc token for {args.team} in that profile's localStorage.")
        if not dval:
            sys.exit("ERROR: no slack `d` cookie found in that profile.")
        d_decoded = urllib.parse.unquote(dval)
        d_encoded = urllib.parse.quote(d_decoded, safe="")
        if not d_decoded.startswith("xoxd-"):
            sys.exit(f"ERROR: `d` cookie decoded to {d_decoded[:12]!r}, not xoxd-*. Stale session?")

        res = auth_test(token, d_encoded)
        if not res.get("ok"):
            sys.exit(f"ERROR: auth.test failed: {res.get('error')}. Session likely expired; "
                     f"have the user reload Slack in that Chrome profile.")
        print(f"[*] auth.test OK — team={res.get('team')} user={res.get('user')} "
              f"id={res.get('team_id')}", file=sys.stderr)
        if not wurl:
            wurl = res.get("url", f"https://{args.team}.slack.com")

        if args.register:
            cmd = ["slackcli", "auth", "login-browser",
                   f"--xoxc={token}", f"--xoxd={d_decoded}",
                   f"--workspace-url={wurl}"]
            if args.name:
                cmd.append(f"--workspace-name={args.name}")
            r = subprocess.run(cmd, capture_output=True, text=True)
            sys.stderr.write(r.stdout + r.stderr)
            if r.returncode != 0:
                sys.exit(f"ERROR: slackcli login failed (exit {r.returncode})")
            print(json.dumps({"ok": True, "registered": True, "team_id": res.get("team_id"),
                              "team": res.get("team"), "url": wurl}))
        else:
            # write secrets to 0600 temp files; print only paths + metadata
            xc = os.path.join(tempfile.gettempdir(), f"slack_{args.team}_xoxc.txt")
            xd = os.path.join(tempfile.gettempdir(), f"slack_{args.team}_xoxd.txt")
            for path, val in ((xc, token), (xd, d_decoded)):
                fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
                os.write(fd, val.encode()); os.close(fd)
            print(json.dumps({"ok": True, "registered": False, "team_id": res.get("team_id"),
                              "team": res.get("team"), "url": wurl,
                              "xoxc_file": xc, "xoxd_file": xd,
                              "hint": "xoxd_file is URL-DECODED, ready for slackcli --xoxd"}))
    finally:
        if proc:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except Exception:
                proc.kill()
        shutil.rmtree(udd, ignore_errors=True)


if __name__ == "__main__":
    main()
