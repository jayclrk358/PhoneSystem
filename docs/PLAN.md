# PhoneSystem — Project Plan

A Linux-based phone system (IP PBX) that Avaya 9608 desk phones register to over
SIP, with SIP trunks to carriers, configured entirely through a web page.

---

## 1. Core decision: use Asterisk, don't write a SIP stack

Writing SIP, RTP, codecs, NAT traversal, DTMF, SRTP and so on from scratch would
take years, and the result would not be reliable.
Instead:

- **Call engine:** Asterisk 22 LTS using the PJSIP channel driver. It is proven,
  free, and handles SIP, RTP, voicemail, conferencing and parking.
- **Our code is everything around it:** the database, config generation,
  phone provisioning, the web UI/API, live status and security.

This is the same model FreePBX uses, but smaller and more modern, and built
around the 9608.

## 2. Avaya 9608 facts that shape the design

| Fact | Consequence |
|---|---|
| 9608s usually ship with **H.323** firmware, and Asterisk can't speak Avaya's H.323 dialect | The phones must be flashed to **Avaya 96x1 SIP firmware** (7.1.x) |
| The SIP firmware is licensed and comes from Avaya support or a reseller | We can't bundle it. The system hosts it once you upload it through the web UI |
| On boot the phone gets DHCP **option 242** (e.g. `HTTPSRVR=10.0.0.5,SIG=2`), then fetches its upgrade script and `46xxsettings.txt` over HTTP | We build an HTTP **provisioning server** and document or automate option 242. `SIG=2` makes the phone switch to SIP |
| `46xxsettings.txt` controls the phone: `SIP_CONTROLLER_LIST 10.0.0.5:5060;transport=tcp`, `ENABLE_AVAYA_ENVIRONMENT 0`, `SIPDOMAIN`, `MWISRVR`, `PSTN_VM_NUM`, `DIALPLAN`, etc. | That file is generated from the database |
| The 96x1 SIP firmware talks SIP over **TCP (or TLS) only, not UDP** | Asterisk gets a TCP transport for the phones. UDP stays available for trunks |
| `SIP_CONTROLLER_LIST` needs a **numeric IPv4 address**, not a hostname | The generator always writes the server's IP |
| Firmware 7.1 supports `FORCE_SIP_USERNAME` / `FORCE_SIP_EXTENSION` / `FORCE_SIP_PASSWORD`, so phones can log in **without anyone typing** | Zero-touch login: assign a phone (by MAC) to an extension in the UI and it logs itself in. **The password is limited to 13 characters**, so fail2ban and IP restrictions matter more |
| The firmware's HTTP client (libcurl) is strict. It rejects some simple HTTP servers (Python `http.server` is known to fail) and marks the download as failed | Provisioning is served by nginx, or by a handler that sends explicit `HTTP/1.1 200`, `Content-Length` and `Connection: close`. Covered by a test |
| The phone only trusts Avaya's own certificate authorities, so it can't do HTTPS provisioning against our server | Provisioning uses plain HTTP and **must be LAN-only**. Firmware images are signed by Avaya, so HTTP is fine for those |
| The craft menu (`Mute 2 7 2 3 8 #`) turns on remote syslog. Manual craft settings override the settings file | Built-in syslog receiver so the UI can show each phone's boot/provisioning log. The docs say to clear phones before deploying |
| Some Avaya-only features don't work with a third-party PBX. See section 2a | Standard SIP features (hold, transfer, conference, MWI lamp, voicemail) are the target. Each one gets tested on a real phone |

## 2a. Buttons, screen and busy lamps on the 9608

The 9608 has 8 line/feature buttons, each with a red/green LED and a label on the
display. It also has 4 softkeys under the display, fixed keys (Hold, Transfer,
Conference, Messages, and others), a message-waiting lamp, and an optional
BM12 button module.

With a non-Avaya PBX, the phone runs in "SIPPING 19" (standard SIP) mode. On real
Avaya systems, button programming and busy lamps come from Avaya's own servers
(PPM and Communication Manager), not from the settings file. Community reports say
`PHONEKEY`-style button settings in `46xxsettings.txt` don't take effect in
third-party mode.

| Item | Status with our PBX |
|---|---|
| Message lamp + Messages key → voicemail | ✅ `MWISRVR` + `PSTN_VM_NUM` |
| Caller name/number on screen | ✅ Asterisk sends P-Asserted-Identity. `DISPLAY_NAME_NUMBER` sets the order |
| Dialing without pressing "Call" | ✅ `DIALPLAN` is generated from our routes |
| Line buttons and their lamps for the phone's **own** calls (ringing/active/hold) | ✅ Native behavior |
| Time, time zone, language, backlight, ring tone | ✅ Settings file (users can also change some on the phone) |
| Relabeling or remapping softkeys / fixed keys | ❌ Set by the firmware |
| Putting features (speed dial, park, paging) on line buttons from the server | ❌ Not in standard mode. Possible only with PPM emulation (below) |
| **Busy lamp field** (a lamp showing another extension's state) | ❌ Not in standard mode. Needs Avaya's proprietary feature-status subscription, which has no public spec |

Ways to get more:

1. **PPM emulation (experimental, optional Phase 6).** Our server pretends to be
   Avaya's Personal Profile Manager and the phone runs in "AST" mode. Community
   projects got button labels, speed dials on the spare buttons, and a contacts
   list this way. But attended transfer and conference **broke** in their tests,
   and busy lamps would still need reverse-engineering. This is treated as a
   research spike, not a promise.
2. **The phone's built-in web browser (WML) and Push interface** (`WMLHOME`,
   `WMLIDLEURI`, `TPSLIST`). Our server could show a live "who's on the phone"
   status page on the display and pop up messages. That isn't lamps, and whether
   the browser works in the SIP firmware **must be checked on a real 9608 early
   in Phase 1**.
3. **A phone with native busy lamps** (Yealink, Poly, Grandstream) for anyone
   who truly needs them, such as a receptionist. Asterisk hints and standard
   busy-lamp subscriptions will be generated for every extension, so those phones
   work out of the box alongside the 9608s.

## 3. Architecture

```
                 ┌──────────────────────── Linux server ────────────────────────┐
  Admin browser ─┼─▶ Web UI (React + TypeScript)                                │
     (HTTPS)     │        │ REST + WebSocket                                     │
                 │        ▼                                                      │
                 │   API service (Python / FastAPI) ─── SQLite (or Postgres)     │
                 │        │ renders configs          │ AMI (live status/control)│
                 │        ▼                          ▼                           │
                 │   /etc/asterisk/generated/*.conf ─▶ Asterisk 22 (PJSIP) ◀────┼──SIP/RTP──▶ SIP trunk
                 │                                        ▲                     │            providers
                 │   Provisioning HTTP server             │                     │
                 │   (46xxsettings.txt, firmware)         │                     │
                 └─────────▲──────────────────────────────┼─────────────────────┘
                           │ HTTP provisioning            │ SIP/RTP
                           └────────── Avaya 9608 phones ─┘
```

### Components

1. **Asterisk 22 LTS (PJSIP):** calls, voicemail (`app_voicemail`), ConfBridge,
   parking, music on hold, CDR.
2. **Config generator:** turns database rows into Jinja2 templates, which render
   `pjsip.generated.conf`, `extensions.generated.conf`, `voicemail.generated.conf`,
   and so on. The generator validates the output, swaps the files in atomically,
   reloads Asterisk over AMI, and keeps the last known-good version for rollback.
   *Why files and not Asterisk Realtime:* files are easier to debug, diff and roll
   back, and reloads are cheap at this scale.
3. **Provisioning service:** generates `46xxsettings.txt` (global settings plus
   per-phone sections) and serves the upgrade script and the uploaded firmware.
   It logs every fetch, so a new phone shows up in the UI by MAC address and can
   be assigned to an extension; the phone then logs itself in (`FORCE_SIP_*`).
   Files are served by nginx over plain HTTP on the LAN, because the phone's HTTP
   client is strict (see section 2). A small syslog receiver collects the phones'
   boot logs for troubleshooting. Optionally it ships a `dnsmasq` DHCP config with
   option 242 if you want this server to hand out addresses to the phones.
4. **API backend:** Python 3.12, FastAPI, SQLAlchemy with Alembic migrations.
   Includes admin authentication (argon2 hashes, sessions, CSRF protection), an
   audit log, and an async AMI client that feeds live registrations, active calls
   and trunk status to the UI over WebSocket.
5. **Web UI:** React, TypeScript and Vite. Pages:
   - Dashboard: registered phones, active calls, trunk status
   - Extensions · Phones (provisioning, firmware) · Trunks
   - Inbound routes (DIDs) · Outbound routes (dial patterns, failover)
   - Ring groups · IVR / auto-attendant · Time conditions
   - Voicemail · Parking / feature codes · Music on hold
   - Call history (CDR) · System (network/NAT, SIP ports, codecs, backups) · Admin users
6. **Packaging:** `install.sh` for Debian 12/13 and Ubuntu 24.04, with systemd
   units, an nftables firewall and fail2ban. Docker Compose is used for
   development and testing (host networking, because of the RTP port range).

## 4. Data model (core)

- `extensions`: number, name, SIP password, caller ID, voicemail on/PIN/email, DND, call forwarding
- `phones`: MAC, model, assigned extension, label, IP, firmware version, last seen
- `trunks`: name, type (`register` or `ip_auth`), host/port/transport, credentials, from-user/domain, codecs, max channels, default caller ID, enabled
- `inbound_routes`: DID, optional caller-ID match, destination
- `outbound_routes`: name, order, dial patterns (prepend/strip), trunk sequence (failover), caller-ID override, allowed-for-extensions
- `ring_groups`, `ivrs` (greeting, digit→destination map), `time_conditions`, `feature_codes`, `moh_classes`
- `system_settings`, `admin_users`, `audit_log`, `config_versions`

A **destination** can point to any of: an extension, ring group, IVR, voicemail
box, time condition, external number, or hang up. Every routing screen uses the
same destination picker.

## 5. Trunking

- **Registration trunks** (most carriers: Telnyx, VoIP.ms, Flowroute, Twilio
  Elastic SIP, …): PJSIP outbound registration plus outbound auth.
- **IP-authenticated trunks:** incoming calls are matched by source IP (`identify`).
- **Inbound:** each DID goes to a destination.
- **Outbound:** pattern-based routes with NANP/E.164 helpers, trunks tried in
  order for failover, per-extension or per-trunk caller ID, and an emergency route
  (911) that can't be disabled by accident.
- **NAT:** external signaling/media address and local networks set in the UI;
  RTP ports 10000–20000.
- **Later:** PBX-to-PBX peer trunks.

## 6. Security (toll fraud is the #1 real-world risk)

- Random SIP passwords are generated automatically. They're capped at 13
  characters for the 9608s (a firmware limit), so the phones' SIP port only
  accepts the local network. Anonymous/guest SIP calls are off.
- fail2ban watches the Asterisk security log. Trunks are restricted to their
  providers' IP addresses.
- International and premium-rate dialing are **off by default** and enabled per
  route, with optional per-extension permissions.
- The web UI runs over HTTPS (self-signed or Let's Encrypt), is limited to the LAN
  by default, and rate-limits login attempts.
- The provisioning server is LAN-only. It has to be plain HTTP (the phone only
  trusts Avaya CAs), and it hands out SIP passwords and the phone admin password.
  Per-phone credentials are only served to that phone's MAC/IP.
- **Later:** TLS + SRTP between the phones and the PBX.

## 7. Phases

| Phase | Scope | Done when |
|---|---|---|
| **0: Foundation** | Repo skeleton, Docker dev environment with Asterisk, lint/test CI, DB migrations | `docker compose up` starts the stack and CI is green |
| **1: Extensions + phones** | **Starts with a hardware check on a real 9608:** firmware version, TCP registration, auto-login, number of line buttons, and whether the browser/Push work. Then extensions CRUD, config generator, provisioning server, 46xxsettings generation, firmware upload, phone discovery | **Two 9608s register and call each other** |
| **2: Trunks + routing** | Trunks, inbound DIDs, outbound routes, caller ID, failover | Calls to and from the outside world work |
| **3: PBX features** | Voicemail + MWI lamp, ring groups, IVR, time conditions, parking, MOH, conferencing, feature codes | Feature checklist passes on a real 9608 |
| **4: Visibility** | Live dashboard (registrations, calls, trunks), call history, optional recording | Dashboard shows live state |
| **5: Hardening + ops** | Installer, firewall/fail2ban, backup/restore, HTTPS, upgrades, TLS/SRTP | Clean install on a fresh Debian box works end to end |
| **6: 9608 extras (optional)** | Status/directory page on the phone's browser plus Push pop-ups (if Phase 1 shows they work). Research spike on PPM emulation for button labels, speed dials and busy lamps | Decide go/no-go from the spike results |

## 8. Testing

- **Unit:** config generation, checked against known-good "golden" files for
  given database states. Dial-pattern and routing logic.
- **Integration:** Asterisk in Docker, with SIPp or PJSUA softphones standing in
  for the phones and for a fake carrier trunk. Automated scripted calls cover
  internal calls, inbound, outbound, voicemail and IVR.
- **Hardware checklist on a real 9608:** registration, hold, blind and attended
  transfer, conference, MWI lamp, voicemail button, call history, re-provisioning.

## 9. Proposed repository layout

```
backend/        FastAPI app: api/, models/, services/ (confgen, ami, provisioning), templates/
frontend/       React + TypeScript (Vite)
asterisk/       Base (static) Asterisk configs that #include the generated files
deploy/         install.sh, systemd units, nftables, fail2ban, dnsmasq example
docker/         Dev/test compose setup
tests/          unit + integration (SIPp/PJSUA scenarios)
docs/           this plan, setup guide, 9608 provisioning guide
```

## 10. What you'll need

- A Linux machine: Debian 12/13 or Ubuntu 24.04. 2 CPU / 2 GB RAM is plenty for dozens of phones.
- **Avaya 96x1 SIP firmware** for the 9608, from Avaya support or a reseller.
- PoE switch (or power bricks) for the phones.
- Access to your router's DHCP settings for option 242, or let this server provide DHCP for the phone network.
- A SIP trunk account with a carrier, for Phase 2.

## 11. Open questions

1. How many phones and sites? This decides SQLite vs Postgres and single vs multi-tenant.
2. Are the phones currently on H.323 or SIP firmware, and which version? Do you have the SIP firmware (7.1.x)?
3. Which SIP trunk provider, if you've picked one?
4. Deployment: native install on the box (recommended), or Docker?
5. Should this server run DHCP for the phones, or will you set option 242 on your existing router?
6. Web stack OK? (FastAPI + React/TypeScript. The alternative is server-rendered HTML with htmx, which is simpler but less interactive.)
7. How important are busy lamps, and for whom (everyone, or just a receptionist)? This decides whether Phase 6 is worth it, or whether one native-BLF phone covers it.
