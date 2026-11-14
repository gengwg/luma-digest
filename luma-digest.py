#!/usr/bin/env python3
"""Email the next two weeks of Luma SF events that match a keyword list.

Reads Luma's SF city iCal feed, the documented "Add iCal Subscription" on
luma.com/sf, so no API key or scraping is involved.

Config:
  LUMA_GMAIL_USER          Gmail address; the digest is sent from and to it
  LUMA_GMAIL_APP_PASSWORD  Google app password for that account

Flags:
  --dry-run  print the digest instead of sending it
"""

import os
import re
import smtplib
import sys
import time
import urllib.request
from datetime import UTC, datetime, timedelta
from email.message import EmailMessage
from zoneinfo import ZoneInfo

FEED = "https://api.luma.com/ics/get?entity=discover&id=discplace-BDj7GNbGlsF7Cka"
TZ = ZoneInfo("America/Los_Angeles")
WEEK = timedelta(days=7)
KEYWORDS = [
    "gpu", "cuda", "nvidia", "kubernetes", "k8s", "cloud native", "cncf",
    "observability", "grafana", "prometheus", "opentelemetry", "sre",
    "devops", "platform engineering", "infra", "infrastructure", "inference",
    "vllm", "sglang", "pytorch", "llm", "founder", "startup",
    "a16z", "speedrun", "edtech", "ed-tech", "education", "chinese",
    "mandarin", "language learning",
]
PATTERN = re.compile(r"\b(" + "|".join(map(re.escape, KEYWORDS)) + r")s?\b", re.I)
TRANSIENT_SMTP_ERRORS = (
    smtplib.SMTPConnectError,
    smtplib.SMTPHeloError,
    smtplib.SMTPServerDisconnected,
)


def retry(operation):
    for attempt in range(3):
        try:
            return operation()
        except (OSError, smtplib.SMTPException) as e:
            if isinstance(e, smtplib.SMTPException) and not isinstance(e, TRANSIENT_SMTP_ERRORS):
                raise
            if attempt == 2:
                raise
            delay = 2**attempt
            print(f"Temporary failure: {e}; retrying in {delay}s", file=sys.stderr)
            time.sleep(delay)


def parse(ics):
    # Unfold before decoding: Luma folds mid-way through multi-byte characters.
    text = re.sub(rb"\r?\n[ \t]", b"", ics).decode()
    events, ev = [], None
    for line in text.splitlines():
        if line == "BEGIN:VEVENT":
            ev = {}
        elif line == "END:VEVENT":
            events.append(ev)
            ev = None
        elif ev is not None and ":" in line:
            key, value = line.split(":", 1)
            ev[key.split(";")[0]] = re.sub(
                r"\\(.)", lambda m: "\n" if m[1] in "nN" else m[1], value
            )
    return events


def start(ev):
    value = ev["DTSTART"]
    if "T" not in value:
        # All-day iCal dates use the calendar's local day, not UTC midnight.
        return datetime.strptime(value, "%Y%m%d").replace(tzinfo=TZ)
    return datetime.strptime(value, "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)


def when(ev):
    dt = start(ev).astimezone(TZ)
    time = "all day" if "T" not in ev["DTSTART"] else f"{dt:%-I:%M%p}"
    return f"{dt:%a %b %-d} {time}"


def pick(events, now):
    # Two weeks because popular events fill up more than a week ahead.
    picked = []
    for ev in events:
        hits = {m.lower() for m in PATTERN.findall(ev["SUMMARY"] + " " + ev.get("DESCRIPTION", ""))}
        if now <= start(ev) < now + 2 * WEEK and hits:
            picked.append((ev, sorted(hits)))
    return sorted(picked, key=lambda p: start(p[0]))


def link(ev):
    # LOCATION is the event URL for online or hidden venues, else a street address.
    m = re.search(r"Get up-to-date information at: (\S+)", ev.get("DESCRIPTION", ""))
    return m[1] if m else ev["LOCATION"]


def render(picked, now):
    sections = []
    for title, lo in [("This week", now), ("Next week", now + WEEK)]:
        entries = [
            f"{when(ev)}  {ev['SUMMARY']}\n"
            f"{link(ev)}\nmatched: {', '.join(hits)}"
            for ev, hits in picked
            if lo <= start(ev) < lo + WEEK
        ]
        sections.append(f"== {title} ==\n\n" + ("\n\n".join(entries) or "No matching events."))
    return "\n\n\n".join(sections)


def main():
    # Luma 403s urllib's default User-Agent.
    req = urllib.request.Request(FEED, headers={"User-Agent": "luma-digest/1.0"})

    def fetch():
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()

    now = datetime.now(UTC)
    body = render(pick(parse(retry(fetch)), now), now)
    if "--dry-run" in sys.argv:
        print(body)
        return
    user = os.environ["LUMA_GMAIL_USER"]
    msg = EmailMessage()
    msg["Subject"] = f"Luma SF next two weeks ({datetime.now(TZ):%b %-d})"
    msg["From"] = msg["To"] = user
    msg.set_content(body)

    def smtp_session():
        s = smtplib.SMTP_SSL("smtp.gmail.com", 465, timeout=30)
        try:
            s.login(user, os.environ["LUMA_GMAIL_APP_PASSWORD"])
        except Exception:
            s.close()
            raise
        return s

    # A lost response after sending is ambiguous; retrying it could duplicate mail.
    with retry(smtp_session) as s:
        s.send_message(msg)


if __name__ == "__main__":
    main()
