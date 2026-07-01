#!/usr/bin/env python3
"""Extract Slack's httpOnly `d` cookie (xoxd-*) from a running Chrome via CDP.

The `d` cookie is httpOnly, so page JavaScript (and claude-in-chrome's network
log) cannot read it. Only the Chrome DevTools Protocol can. The superpowers-chrome
browser exposes CDP on localhost:9222.

The `d` cookie is domain-wide (.slack.com) — the SAME value works for every
workspace in that browser profile. Extract once, reuse for any team.

Usage:
  python3 extract_xoxd.py            # print URL-ENCODED value (for curl Cookie header)
  python3 extract_xoxd.py --decoded  # print URL-DECODED value (for `slackcli auth login-browser`)
  python3 extract_xoxd.py --port 9222

Exit 1 with a diagnostic if CDP is unreachable or no `d` cookie is found.
"""
import argparse, base64, json, os, socket, struct, sys, urllib.parse, urllib.request


def ws_handshake(host, port, path):
    s = socket.create_connection((host, port))
    key = base64.b64encode(os.urandom(16)).decode()
    req = (f"GET {path} HTTP/1.1\r\nHost: {host}:{port}\r\nUpgrade: websocket\r\n"
           f"Connection: Upgrade\r\nSec-WebSocket-Key: {key}\r\nSec-WebSocket-Version: 13\r\n\r\n")
    s.sendall(req.encode())
    resp = b""
    while b"\r\n\r\n" not in resp:
        resp += s.recv(4096)
    return s


def ws_send(s, msg):
    data = msg.encode()
    frame = bytearray([0x81])
    l = len(data)
    mk = os.urandom(4)
    if l < 126:
        frame.append(0x80 | l)
    elif l < 65536:
        frame.append(0x80 | 126)
        frame.extend(struct.pack(">H", l))
    else:
        frame.append(0x80 | 127)
        frame.extend(struct.pack(">Q", l))
    frame.extend(mk)
    frame.extend(bytearray(b ^ mk[i % 4] for i, b in enumerate(data)))
    s.sendall(frame)


def _recvn(s, n):
    data = b""
    while len(data) < n:
        chunk = s.recv(n - len(data))
        if not chunk:
            break
        data += chunk
    return data


def _recv_frame(s):
    """Return (fin, opcode, payload) for one WebSocket frame (server->client, unmasked)."""
    hdr = _recvn(s, 2)
    if len(hdr) < 2:
        return True, 0x8, b""
    fin = bool(hdr[0] & 0x80)
    opcode = hdr[0] & 0x0F
    length = hdr[1] & 0x7F
    if length == 126:
        length = struct.unpack(">H", _recvn(s, 2))[0]
    elif length == 127:
        length = struct.unpack(">Q", _recvn(s, 8))[0]
    return fin, opcode, _recvn(s, length)


def ws_recv_message(s):
    """Reassemble a full text message across continuation frames; skip ping/pong."""
    payload = b""
    while True:
        fin, opcode, data = _recv_frame(s)
        if opcode in (0x9, 0xA):   # ping/pong control frame — ignore
            continue
        if opcode == 0x8:          # close
            return payload.decode(errors="replace")
        payload += data
        if fin:
            return payload.decode(errors="replace")


def ws_recv_response(s, want_id, tries=20):
    """Read messages until we get the CDP reply matching want_id (skip async events)."""
    for _ in range(tries):
        txt = ws_recv_message(s)
        if not txt:
            continue
        try:
            obj = json.loads(txt)
        except ValueError:
            continue
        if obj.get("id") == want_id:
            return obj
    return {}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=9222)
    ap.add_argument("--decoded", action="store_true",
                    help="print URL-decoded value (for slackcli); default is URL-encoded (for curl)")
    args = ap.parse_args()

    try:
        ver = json.loads(urllib.request.urlopen(
            f"http://localhost:{args.port}/json/version", timeout=5).read())
    except Exception as e:
        sys.exit(f"ERROR: CDP unreachable on localhost:{args.port} ({e}). "
                 f"Is the superpowers-chrome browser running with Slack open?")

    path = ver["webSocketDebuggerUrl"].split(f"localhost:{args.port}")[1]
    s = ws_handshake("localhost", args.port, path)
    ws_send(s, json.dumps({"id": 1, "method": "Storage.getCookies"}))
    resp = ws_recv_response(s, 1)
    s.close()

    for c in resp.get("result", {}).get("cookies", []):
        if c["name"] == "d" and "slack" in c.get("domain", ""):
            v = c["value"]
            # `d` value is URL-encoded; decoded form starts with `xoxd-`.
            if not urllib.parse.unquote(v).startswith("xoxd-"):
                sys.exit(f"ERROR: found a slack `d` cookie but it doesn't look like "
                         f"xoxd-* (got {urllib.parse.unquote(v)[:12]!r}). Session may be stale.")
            print(urllib.parse.unquote(v) if args.decoded else v)
            return
    sys.exit("ERROR: no `d` cookie for a slack domain found. Open/sign into Slack "
             "in the superpowers-chrome browser first.")


if __name__ == "__main__":
    main()
