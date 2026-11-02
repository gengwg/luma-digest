# luma-digest

Emails the next two weeks of [Luma SF](https://luma.com/sf) events that match a keyword list, every Saturday at 8am Pacific.
Popular events fill up early, so the digest looks two weeks ahead, split into "This week" and "Next week".
One stdlib-only Python script reading Luma's public SF iCal feed, run by a systemd user timer.
Edit `KEYWORDS` in the script to tune what matches.

## Install

Create a Google app password at https://myaccount.google.com/apppasswords (needs 2-Step Verification).

```bash
cd ~/projects/luma-digest
install -Dm755 luma-digest.py ~/.local/bin/luma-digest.py
install -Dm600 env.example ~/.config/luma-digest/env   # then set the app password
cp systemd/luma-digest.{service,timer} ~/.config/systemd/user/
systemctl --user daemon-reload
systemctl --user enable --now luma-digest.timer
systemctl --user start luma-digest   # send a test email now
```

`Persistent=true` sends a missed digest when the machine next wakes.

## Use

```bash
./luma-digest.py --dry-run                 # print the digest
systemctl --user start luma-digest         # send one now
python3 -m unittest test_luma_digest.py
```
