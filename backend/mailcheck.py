"""`python -m backend.mailcheck` — prove the mail setup before trusting it.

Email fails silently. A missing DKIM record, an SPF record with a typo, a
provider that was never verified: none of them raise anything, and the only
symptom is that a player says they never got the link, days later, with nothing
in the log to explain it. This checks each layer separately and says which one
is wrong.

    python -m backend.mailcheck                      # config + DNS
    python -m backend.mailcheck --send you@you.com   # and actually send one

Run it ON THE SERVER, with the service's environment, since that is the
configuration that matters:

    sudo systemctl show relay-game.service -p Environment
    sudo -u relay RELAY_BASE_URL=... python -m backend.mailcheck

DNS is queried over UDP with the standard library rather than `dig` or
dnspython. A VPS often has neither, and this repo's dependency list is two
packages and worth keeping that way.
"""

from __future__ import annotations

import argparse
import os
import random
import socket
import struct
import sys

from backend import mailer

# Public resolvers, tried in order. Not the system resolver: on a VPS that is
# often a caching stub that answers from a stale zone, which is exactly the
# wrong answer when the question is "did my new DNS record propagate".
RESOLVERS = ("1.1.1.1", "8.8.8.8", "9.9.9.9")

TYPE_TXT, TYPE_CNAME = 16, 5

# Selectors the common providers publish DKIM under. DKIM lives at
# <selector>._domainkey.<domain> and the selector is chosen by whoever signs,
# so there is nothing to derive it from — it has to be guessed or told.
KNOWN_SELECTORS = (
    "resend", "postmark", "pm", "s1", "s2", "mailgun", "k1", "google",
    "default", "selector1", "selector2", "sendgrid", "zoho", "mail",
)

GREEN, RED, YELLOW, DIM, RESET = "\033[32m", "\033[31m", "\033[33m", "\033[2m", "\033[0m"
if not sys.stdout.isatty():
    GREEN = RED = YELLOW = DIM = RESET = ""


# --- a very small DNS client ----------------------------------------------

def _encode_name(name: str) -> bytes:
    parts = [label.encode() for label in name.rstrip(".").split(".")]
    return b"".join(bytes([len(p)]) + p for p in parts) + b"\x00"


def _skip_name(data: bytes, offset: int) -> int:
    """Step over a name, following the compression pointers DNS packs names
    with (a length byte of 0b11xxxxxx means "the rest is back there")."""
    while True:
        length = data[offset]
        if length == 0:
            return offset + 1
        if length & 0xC0 == 0xC0:
            return offset + 2        # a pointer is two bytes and ends the name
        offset += 1 + length


def _ask_udp(request: bytes, resolver: str, timeout: float) -> bytes | None:
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    sock.settimeout(timeout)
    try:
        sock.sendto(request, (resolver, 53))
        return sock.recvfrom(4096)[0]
    except OSError:
        return None
    finally:
        sock.close()


def _ask_tcp(request: bytes, resolver: str, timeout: float) -> bytes | None:
    """The same question over TCP, where the answer is length-prefixed.

    Required, not an optimisation: a UDP answer that does not fit comes back
    with the truncation bit set and NO records, and retrying over TCP is the
    only way to read it. Domains with a pile of TXT records (every SaaS
    verification token lives there) and DKIM keys, which are long by nature,
    both cross that line routinely.
    """
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect((resolver, 53))
        sock.sendall(struct.pack(">H", len(request)) + request)
        header = sock.recv(2)
        if len(header) < 2:
            return None
        remaining = struct.unpack(">H", header)[0]
        chunks = []
        while remaining > 0:
            chunk = sock.recv(remaining)
            if not chunk:
                break
            chunks.append(chunk)
            remaining -= len(chunk)
        return b"".join(chunks)
    except OSError:
        return None
    finally:
        sock.close()


def query(name: str, record_type: int = TYPE_TXT, timeout: float = 4.0) -> list[str]:
    """Every record of `record_type` at `name`. Empty list if there are none.

    Deliberately forgiving: a resolver that times out or answers rubbish moves
    on to the next one, because "I could not check" and "the record is missing"
    are different answers and only the second one is the user's problem.
    """
    request = struct.pack(">HHHHHH", random.randint(0, 0xFFFF), 0x0100, 1, 0, 0, 0)
    request += _encode_name(name) + struct.pack(">HH", record_type, 1)

    for resolver in RESOLVERS:
        data = _ask_udp(request, resolver, timeout)
        if data is None:
            continue
        # TC bit: the answer did not fit in a UDP packet. Ask again over TCP.
        if len(data) >= 12 and struct.unpack(">H", data[2:4])[0] & 0x0200:
            data = _ask_tcp(request, resolver, timeout) or data

        try:
            _, _, qd, an, _, _ = struct.unpack(">HHHHHH", data[:12])
            offset = 12
            for _ in range(qd):                       # step over the question
                offset = _skip_name(data, offset) + 4
            found: list[str] = []
            for _ in range(an):
                offset = _skip_name(data, offset)
                rtype, _, _, rdlength = struct.unpack(">HHIH", data[offset:offset + 10])
                offset += 10
                rdata = data[offset:offset + rdlength]
                offset += rdlength
                if rtype == record_type == TYPE_TXT:
                    # TXT rdata is one or more length-prefixed chunks; a record
                    # longer than 255 bytes (every DKIM key) arrives split.
                    text, cursor = "", 0
                    while cursor < len(rdata):
                        size = rdata[cursor]
                        text += rdata[cursor + 1:cursor + 1 + size].decode(
                            "utf-8", "replace")
                        cursor += 1 + size
                    found.append(text)
                elif rtype == record_type == TYPE_CNAME:
                    found.append("<cname>")
            if found:
                return found
        except (struct.error, IndexError):
            continue
    return []


# --- reporting ------------------------------------------------------------

def ok(message: str) -> None:
    print(f"  {GREEN}PASS{RESET}  {message}")


def bad(message: str, fix: str = "") -> None:
    print(f"  {RED}FAIL{RESET}  {message}")
    if fix:
        print(f"        {DIM}{fix}{RESET}")


def warn(message: str, fix: str = "") -> None:
    print(f"  {YELLOW}WARN{RESET}  {message}")
    if fix:
        print(f"        {DIM}{fix}{RESET}")


def check_config() -> int:
    print("\nConfiguration")
    backend = os.environ.get("RELAY_MAIL_BACKEND", "console")
    problems = mailer.configuration_problems()
    # Only a development console backend is worth a note rather than an error;
    # on a public base URL it lands in `problems` below as a refusal.
    if backend == "console" and not problems:
        warn("RELAY_MAIL_BACKEND is 'console' — mail prints to the log, nothing is sent.",
             "That is correct for development. Set it to 'smtp' to send for real.")
    for problem in problems:
        bad(problem)
    if not problems:
        ok(f"base URL: {mailer.base_url()}")
        ok(f"backend: {backend}"
           + (f" via {os.environ.get('RELAY_SMTP_HOST')}:{mailer._smtp_port()}"
              f" ({mailer._security()})" if backend == "smtp" else ""))
    return len(problems)


def check_dns(domain: str, selector: str | None) -> int:
    """SPF, DKIM and DMARC for the domain mail claims to come from."""
    print(f"\nDNS for {domain}")
    failures = 0

    failures += _check_spf(domain)

    # DKIM. Resolved as its own step rather than inline, so that finding (or
    # not finding) a key never short-circuits the DMARC check below it.
    failures += _check_dkim(domain, selector)

    dmarc = [txt for txt in query(f"_dmarc.{domain}")
             if txt.lower().startswith("v=dmarc1")]
    if not dmarc:
        warn(f"no DMARC record at _dmarc.{domain}",
             'Add a TXT record: "v=DMARC1; p=none;" — start at p=none and tighten later.')
    else:
        ok(f"DMARC: {dmarc[0][:70]}")
    return failures


# Where an envelope sender commonly lives when it is not the From domain
# itself. A provider that bounces through its own infrastructure puts the
# return path on a subdomain and publishes SPF there.
ENVELOPE_PREFIXES = ("send", "mail", "bounce", "em", "pm-bounces")


def _check_spf(domain: str) -> int:
    """SPF for the domain mail is sent from.

    Checked at the From domain AND at the usual envelope subdomains, because
    SPF is evaluated against the ENVELOPE sender (the Return-Path), not the
    From header a reader sees. Resend, Postmark and SES-backed senders all
    route bounces through `send.<domain>` or similar and publish SPF there, so
    looking only at the From domain reports a working setup as broken.
    """
    for candidate in (domain, *(f"{prefix}.{domain}" for prefix in ENVELOPE_PREFIXES)):
        records = [txt for txt in query(candidate) if txt.lower().startswith("v=spf1")]
        if len(records) > 1:
            bad(f"{len(records)} SPF records at {candidate} — a domain may have exactly one",
                "Merge them into a single record; two is treated by receivers as none.")
            return 1
        if records:
            where = "" if candidate == domain else f" (envelope domain {candidate})"
            ok(f"SPF{where}: {records[0][:66]}")
            return 0
    bad(f"no SPF record at {domain} or its usual envelope subdomains",
        'Add a TXT record: "v=spf1 include:<your provider> ~all". If your '
        "provider bounces through a subdomain, it belongs there instead.")
    return 1


def _check_dkim(domain: str, selector: str | None) -> int:
    """Find a usable DKIM key, and say which way it is unusable if it is not.

    A key lives at <selector>._domainkey.<domain>, and nothing about the domain
    reveals the selector, so it is either supplied or guessed from the list the
    common providers use.
    """
    selectors = [selector] if selector else KNOWN_SELECTORS
    revoked = ""
    pending = ""
    for candidate in selectors:
        host = f"{candidate}._domainkey.{domain}"
        records = query(host)
        for record in records:
            # `p=` carries the public key. Present but empty means the key was
            # REVOKED — the record exists, and mail signed with it still fails.
            # Accepting any TXT at this name would call that a pass.
            if "p=" in record and record.split("p=", 1)[1].strip(" ;"):
                ok(f"DKIM found at {host}")
                return 0
            if record.lower().startswith("v=dkim1"):
                revoked = host
        if not records and query(host, TYPE_CNAME):
            pending = host

    if revoked:
        bad(f"DKIM record at {revoked} has an empty key (p=), which means revoked",
            "Re-issue the DKIM key with your provider and replace the record.")
        return 1
    if pending:
        warn(f"{pending} is a CNAME that does not resolve to a key yet",
             "Normal for a few minutes after adding it; check again shortly.")
        return 0
    bad(f"no DKIM record found for {domain}",
        "Tried: " + ", ".join(f"{s}._domainkey" for s in selectors[:6])
        + ". Pass --selector if yours differs.")
    return 1


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Check the mail setup: configuration, DNS, and optionally a real send.")
    parser.add_argument("--send", metavar="ADDRESS",
                        help="after the checks pass, send a real test email here")
    parser.add_argument("--selector", help="DKIM selector, if it is not a common one")
    parser.add_argument("--skip-dns", action="store_true")
    args = parser.parse_args()

    print(f"{DIM}The Relay: mail check{RESET}")
    failures = check_config()

    domain = mailer._sender_domain()
    if args.skip_dns:
        pass
    elif mailer.is_local_url(f"https://{domain}"):
        warn(f"sender domain is {domain!r}, which is not a real domain — skipping DNS.",
             "Set RELAY_MAIL_FROM to an address at a domain you control.")
    else:
        failures += check_dns(domain, args.selector)

    if args.send:
        print(f"\nTest send to {args.send}")
        if failures:
            warn("sending anyway, but the checks above say it will probably bounce.")
        mail = mailer.send(
            args.send, "The Relay: mail check",
            "If you are reading this, The Relay can send email.\n\n"
            f"Sent from {mailer.base_url()}.\n", kind="check")
        if mail.delivered:
            ok("accepted by the mail server — check the inbox, and spam.")
        else:
            failures += 1
            bad("the send failed. The reason is logged above this line.")

    print()
    if failures:
        print(f"{RED}{failures} problem(s).{RESET} See docs/ACCOUNTS.md and .env.example.")
    else:
        print(f"{GREEN}All checks passed.{RESET}")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())
