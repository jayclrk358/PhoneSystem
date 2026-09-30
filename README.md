# PhoneSystem

A Linux phone system (IP PBX) for **Avaya 9608** desk phones, configured entirely
from a web page. It uses [Asterisk](https://www.asterisk.org/) as the call engine
and adds everything around it: phone provisioning, a database of extensions and
phones, config generation with rollback, and the web UI.

**Status:** Phase 1 (extensions and phones) is built and tested with simulated
phones; the next step is checking it on a real 9608. See
[docs/PLAN.md](docs/PLAN.md) for the full plan (SIP trunks, voicemail, IVR,
button remapping, screen customization, …).

## What works now

- Extensions, each with a generated SIP password (9608-compatible).
- Phones find the server through DHCP option 242 and download a generated
  `46xxsettings.txt`. Phones assigned to an extension **log in automatically**.
- New phones show up on their own (identified by MAC address). Model and
  firmware version are detected.
- Upload of the Avaya firmware zip, served to the phones (including H.323 → SIP
  conversion).
- Phone logs (syslog) and every provisioning request are visible per phone.
- Changes go live with **Apply changes**. Every applied config is versioned;
  a failed reload is rolled back automatically, and you can roll back by hand.
- **Call tracking:** every call (answered, missed, busy, failed) is logged and
  labelled incoming / outgoing external / internal. Search or look up a number,
  filter by extension, direction and result, export to CSV, and see totals,
  calls per day and per-extension counts (made, received, missed, talk time).
- **Live updates:** pages refresh themselves the moment something happens (a
  call ends, a phone registers, fetches its settings or logs something, another
  admin makes a change), with a "Live" indicator in the top bar.
- Live status: which phones are registered and whether Asterisk is running.
- Admin accounts, sign-in throttling, CSRF protection, audit log.

## Install

```bash
sudo ./deploy/install.sh     # Ubuntu 24.04 LTS
```

Then open `https://<server-ip>/`. Full guide: [docs/SETUP.md](docs/SETUP.md).
Checklist for a real phone: [docs/HARDWARE_CHECKLIST.md](docs/HARDWARE_CHECKLIST.md).

## Development

Needs Python 3.11+, Node.js 22, and (for the dev stack and integration tests)
the `asterisk` and `sip-tester` packages.

```bash
make setup              # backend venv + UI packages
make dev                # private Asterisk + app on http://127.0.0.1:8000
make check              # lint, unit tests, Asterisk/SIPp integration tests, UI tests + build
```

For UI work, run `npm run dev` in `frontend/` alongside `make dev`; it proxies
`/api` to port 8000.

| Path | What's there |
|---|---|
| `backend/phonesystem/` | FastAPI app: `api/`, `services/` (config generator, AMI client, provisioning server, syslog, firmware), templates, migrations |
| `backend/tests/` | unit tests (incl. golden config files) and integration tests against a real Asterisk with SIPp phones |
| `frontend/` | React + TypeScript web UI |
| `asterisk/` | base Asterisk configs; they `#include` the generated files |
| `deploy/` | installer, systemd unit, dnsmasq example |
| `docs/` | plan, setup guide, hardware checklist |
