# PhoneSystem — Project Plan

A Linux-based phone system (IP PBX) that Avaya 9608 desk phones register to over
SIP, with SIP trunks to carriers, configured entirely through a web page.

## Status

| Phase | State |
|---|---|
| 0: Foundation | **Done.** Repo layout, `make dev`, CI (lint, unit, real-Asterisk integration, UI) |
| 1: Extensions + phones | **Built and tested in simulation; waiting on the hardware check.** Extensions, phone provisioning (`46xxsettings.txt` with auto-login), firmware upload, phone discovery, phone logs, config apply/rollback, web UI, installer. Two simulated 9608s (SIPp over TCP) register and call each other in the integration tests. Next: run [HARDWARE_CHECKLIST.md](HARDWARE_CHECKLIST.md) on a real 9608 |
| Extra (from Phase 6, done early on request) | **Done.** Call tracking (call log, number lookup, per-extension and daily stats, CSV export) and live page updates (Server-Sent Events) |
| 2 onward | Not started |

Changes from the original plan, decided while building Phase 0/1:
- **Asterisk 20 LTS** from Ubuntu 24.04's own packages instead of 22. It's
  supported until late 2027 and needs no source build. The generated config
  also works on 22.
- **No nginx.** The phone-facing server is our own small HTTP server that
  writes classic, explicit responses, because it has to decide per phone what to
  send (auto-login lines only go to the phone they belong to). curl (the same
  libcurl family the phones use) is tested against it.
- **`make dev` instead of Docker Compose** for development. It runs a private
  Asterisk plus the app with no Docker needed. Docker support can come later.
- **How phones are identified:** the server looks up the requesting IP in its
  own ARP table, which works when phones are on the same network segment (VLAN)
  as the server. A MAC address in the User-Agent (if the firmware sends one) is
  used to list the phone, but **auto-login credentials only go to phones whose
  MAC the ARP table confirms**. Otherwise anyone on the LAN could claim a phone's
  MAC and receive its SIP password. Phones on another segment still get the
  shared settings, and people log in on the keypad.

---

## 1. Core decision: use Asterisk, don't write a SIP stack

Writing SIP, RTP, codecs, NAT traversal, DTMF, SRTP and so on from scratch would
take years, and the result would not be reliable.
Instead:

- **Call engine:** Asterisk 20+ LTS using the PJSIP channel driver. It is proven,
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
| The firmware's HTTP client (libcurl) is strict. It rejects some simple HTTP servers (Python `http.server` is known to fail) and marks the download as failed | Provisioning uses our own server that sends explicit `HTTP/1.1 200`, `Content-Length`, `Last-Modified` and `Connection: close`. Covered by byte-level tests and a curl test |
| The phone only trusts Avaya's own certificate authorities, so it can't do HTTPS provisioning against our server | Provisioning uses plain HTTP and **must be LAN-only**. Firmware images are signed by Avaya, so HTTP is fine for those |
| The craft menu (`Mute 2 7 2 3 8 #`) turns on remote syslog. Manual craft settings override the settings file | Built-in syslog receiver so the UI can show each phone's boot/provisioning log. The docs say to clear phones before deploying |
| Some Avaya-only features don't work with a third-party PBX. See section 2a | Standard SIP features (hold, transfer, conference, MWI lamp, voicemail) are the target. Each one gets tested on a real phone |

## 2a. Buttons and screen (core goal)

Remapping buttons and customizing the screen are **core requirements**. Busy lamps
(seeing another extension's state) are **not needed**.

The 9608 has 8 line/feature buttons, each with a red/green LED and a label on the
display (more pages by scrolling). It also has 4 softkeys under the display, fixed
keys (Hold, Transfer, Conference, Messages, and others), a message-waiting lamp,
and an optional BM12 button module.

### Why buttons are the hard part

In normal third-party ("SIPPING 19") mode the button layout is fixed: the buttons
are just lines. Community reports say `PHONEKEY`-style settings in
`46xxsettings.txt` are ignored.

On a real Avaya system, the phone runs in "AST" mode. After it registers, it
downloads its button layout, contacts and other data from **PPM (Personal Profile
Manager)**, a SOAP web service that normally runs on Avaya Session Manager. PPM
can be served over **plain HTTP (port 80)**, so we can implement it ourselves
without needing Avaya's certificates.

### Track A (main plan): our own PPM service

- The phone is switched to AST mode in `46xxsettings.txt`. After it registers, it
  calls our PPM endpoint, and we answer with the button layout designed in the
  web UI.
- Button types we'll offer, each with a **custom label**:
  - **Line** (call appearance)
  - **Speed dial** (an external number or an internal extension)
  - **Feature**: DND on/off, call forward, park/retrieve, pickup, voicemail,
    paging, a conference room, and so on. Each one is implemented as a speed
    dial of an Asterisk feature code.
  - **Blank**
- *Why feature buttons are speed dials of feature codes:* real Avaya feature
  buttons are invoked through Communication Manager signaling that would have to
  be reverse-engineered. Speed-dialing a feature code does the same job. The only
  thing lost is the lamp showing the feature's state, and you've said lamps
  aren't needed.
- **Bonus:** PPM also delivers the **contacts list**, so the company directory
  from the web UI shows up on every phone.
- **Known risk:** community projects running phones in AST mode found that
  **attended transfer and conference failed**, sometimes freezing the phone. The
  first job in this track is to capture exactly what the phone sends in AST mode
  for transfer and conference, and make it work. That fix goes either in the
  Asterisk config or in a thin SIP adapter in front of Asterisk. We'll also answer
  the phone's Avaya-specific SIP subscriptions cleanly so it doesn't show errors.
- **Reference material:** Avaya's *PPM Interface Specification* and WSDL (Avaya
  DevConnect, free registration), and a community PHP PPM server with Asterisk
  patches (FreePBX forum, "Avaya 96x1 extended features").
- **Go/no-go gate:** if transfer and conference can't be made reliable in AST
  mode, we fall back to Track B.

### Track B (fallback): standard mode

The server can't push button layouts in standard mode. Firmware 7.x lets a
**user** add Speed dial or Feature buttons from the phone itself (on a blank
button: Custom → Add). Whether that works in third-party mode will be tested in
Phase 1. If it does, the web UI can still manage labels and feature codes as a
printable/reference layout, but someone has to key it in on each phone.

### Screen customization (works in either mode)

| Item | How |
|---|---|
| Logo / screensaver image | `LOGOS` + `CURRENT_LOGO` (SIP firmware). The web UI converts an uploaded image to the 9608's small monochrome format |
| Screensaver timeout, backlight, language, date/time format, name/number order | Settings file (`SCREENSAVERON`, `DISPLAY_NAME_NUMBER`, …) |
| **Custom idle screen** | The phone's built-in web browser shows a page from our server when idle (`WMLIDLEURI`), designed in the web UI (company name, date, the user's extension, announcements) |
| **Phone apps** on the phone's browser (`WMLHOME`) | Company directory with click-to-dial, DND and call-forward toggles, voicemail and call-history views, custom menus |
| Pop-up messages from the web UI | Avaya Push interface (`TPSLIST`, `SUBSCRIBELIST`): text alerts and announcements sent to one phone, a group, or all phones |
| Main call screen layout, softkey row, fixed keys, fonts | ❌ Set by the firmware; can't be changed |

The 9608 SIP 7.x user guides document the browser, but Push and the idle-screen
behavior still have to be confirmed on your phone in Phase 1.

If busy lamps are ever needed later, any standard phone with native busy lamps
(Yealink, Poly, Grandstream) will work, because Asterisk hints are generated for
every extension anyway.

## 3. Architecture

```
                 ┌──────────────────────── Linux server ────────────────────────┐
  Admin browser ─┼─▶ Web UI (React + TypeScript)                                │
     (HTTPS)     │        │ REST + WebSocket                                     │
                 │        ▼                                                      │
                 │   API service (Python / FastAPI) ─── SQLite (or Postgres)     │
                 │        │ renders configs          │ AMI (live status/control)│
                 │        ▼                          ▼                           │
                 │   /etc/asterisk/phonesystem/*.conf ─▶ Asterisk 20+ (PJSIP) ◀──┼──SIP/RTP──▶ SIP trunk
                 │                                        ▲                     │            providers
                 │   Phone-facing HTTP (LAN only):        │                     │
                 │   · provisioning (46xxsettings, fw)    │                     │
                 │   · PPM service (buttons, contacts)    │                     │
                 │   · phone apps (WML) + Push            │                     │
                 └─────────▲──────────────────────────────┼─────────────────────┘
                           │ HTTP (config, PPM, apps)     │ SIP/RTP
                           └────────── Avaya 9608 phones ─┘
```

### Components

1. **Asterisk 20+ LTS (PJSIP):** calls, voicemail (`app_voicemail`), ConfBridge,
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
   Files are served over plain HTTP on the LAN by our own strict server, because
   the phone's HTTP client is picky (see section 2). A small syslog receiver collects the phones'
   boot logs for troubleshooting. Optionally it ships a `dnsmasq` DHCP config with
   option 242 if you want this server to hand out addresses to the phones.
4. **PPM service:** a SOAP-over-HTTP endpoint that imitates Avaya's Personal
   Profile Manager. It gives each phone its button layout (labels, speed dials,
   feature buttons) and the contacts list (see section 2a, Track A).
5. **Phone apps + Push:** WML pages served to the phone's browser (idle screen,
   directory, DND and forward toggles, and so on), plus a Push sender for
   pop-up messages.
6. **API backend:** Python 3.11+, FastAPI, SQLAlchemy with Alembic migrations.
   Includes admin authentication (argon2 hashes, sessions, CSRF protection), an
   audit log, and an async AMI client that feeds live registrations, active calls
   and trunk status to the UI over WebSocket.
7. **Web UI:** React, TypeScript and Vite. Pages:
   - Dashboard: registered phones, active calls, trunk status
   - Extensions · Phones (provisioning, firmware) · Trunks
   - **Button designer:** a picture of the 9608 (and BM12) where you click a
     button and set its type, label and target. Layouts are saved as templates
     and assigned to phones or users, with per-phone overrides
   - **Screen & branding:** logo, screensaver, idle-screen designer, phone apps
   - **Messages:** send pop-up messages to phones
   - Inbound routes (DIDs) · Outbound routes (dial patterns, failover)
   - Ring groups · IVR / auto-attendant · Time conditions
   - Voicemail · Parking / feature codes · Music on hold
   - Call history (CDR) · System (network/NAT, SIP ports, codecs, backups) · Admin users
8. **Packaging:** `deploy/install.sh` for Ubuntu 24.04 (or any Debian-based
   system with an Asterisk 20+ package) and a hardened systemd unit. The
   nftables firewall and fail2ban come in Phase 7. `make dev` runs a private
   Asterisk plus the app for development.

## 4. Data model (core)

- `extensions`: number, name, SIP password, caller ID, voicemail on/PIN/email, DND, call forwarding
- `phones`: MAC, model, assigned extension, label, IP, firmware version, last seen
- `trunks`: name, type (`register` or `ip_auth`), host/port/transport, credentials, from-user/domain, codecs, max channels, default caller ID, enabled
- `inbound_routes`: DID, optional caller-ID match, destination
- `outbound_routes`: name, order, dial patterns (prepend/strip), trunk sequence (failover), caller-ID override, allowed-for-extensions
- `button_layouts`: name, model (9608 / 9608 + BM12), assigned to phones or extensions
- `buttons`: layout, position (page/slot, including module), type (`line` / `speed_dial` / `feature` / `blank`), label, target (number or feature)
- `screen_profiles`: logo image, screensaver timeout, backlight, language, idle-screen content, enabled phone apps
- `contacts`: company directory (also pushed to phones via PPM)
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
  Per-phone credentials are only served to that phone's MAC/IP. The same
  applies to PPM and the phone apps.
- **Later:** TLS + SRTP between the phones and the PBX.

## 7. Phases

| Phase | Scope | Done when |
|---|---|---|
| **0: Foundation** | Repo skeleton, dev stack with a private Asterisk, lint/test CI, DB migrations | `make dev` starts the stack and CI is green |
| **1: Extensions + phones** | **Starts with a hardware check on a real 9608:** firmware version, TCP registration, auto-login, logo, whether the browser and Push work, whether Custom → Add works in standard mode, and a capture of the AST-mode PPM requests. Then extensions CRUD, config generator, provisioning server, 46xxsettings generation, firmware upload, phone discovery | **Two 9608s register and call each other** |
| **2: Button remapping (PPM)** | PPM service, AST-mode transfer/conference fix, button designer UI, layout templates, contacts sync | **Go/no-go gate:** a layout designed in the web UI shows up on the phone, and transfer and conference work in AST mode. If not, switch to Track B |
| **3: Screen + phone apps** | Logo/screensaver upload and conversion, idle-screen designer, WML phone apps, Push messages | Branding, idle screen and apps show on a real 9608 |
| **4: Trunks + routing** | Trunks, inbound DIDs, outbound routes, caller ID, failover | Calls to and from the outside world work |
| **5: PBX features** | Voicemail + MWI lamp, ring groups, IVR, time conditions, parking, MOH, conferencing, the feature codes the buttons use | Feature checklist passes on a real 9608 |
| **6: Visibility** | Live dashboard (registrations, calls, trunks), call history, optional recording | Dashboard shows live state |
| **7: Hardening + ops** | Installer, firewall/fail2ban, backup/restore, HTTPS for the admin UI, upgrades, TLS/SRTP | Clean install on a fresh Debian box works end to end |

Buttons come before trunks on purpose. Whether the phones run in AST mode or
standard mode affects how transfer, conference and feature codes are built, so
it has to be settled early.

## 8. Testing

- **Unit:** config generation, checked against known-good "golden" files for
  given database states. Dial-pattern and routing logic. PPM responses checked
  against SOAP captured from a real phone, and against the Avaya spec.
- **Integration:** a private real Asterisk, with SIPp scenarios standing in
  for the phones and for a fake carrier trunk. Automated scripted calls cover
  internal calls, inbound, outbound, voicemail and IVR.
- **Hardware checklist on a real 9608:** registration, hold, blind and attended
  transfer, conference (in AST mode too), MWI lamp, voicemail button, call history,
  re-provisioning. Plus: button layout and labels appear, every speed dial and
  feature button works, the contacts list syncs, and logo, idle screen, apps and
  Push messages display.

## 9. Proposed repository layout

```
backend/        FastAPI app: phonesystem/{api,services,templates,migrations}, tests/, scripts/devstack.py
frontend/       React + TypeScript (Vite)
asterisk/       Base (static) Asterisk configs that #include the generated files
deploy/         install.sh, systemd unit, dnsmasq example (firewall/fail2ban in Phase 7)
docs/           this plan, setup guide, 9608 hardware checklist
```

## 10. What you'll need

- A Linux machine: Debian 12/13 or Ubuntu 24.04. 2 CPU / 2 GB RAM is plenty for dozens of phones.
- **Avaya 96x1 SIP firmware** for the 9608, from Avaya support or a reseller.
- PoE switch (or power bricks) for the phones.
- Access to your router's DHCP settings for option 242, or let this server provide DHCP for the phone network.
- A SIP trunk account with a carrier, for Phase 4.
- Ideally, Avaya's *PPM Interface Specification* from Avaya DevConnect (free
  registration). It makes the button work much faster and safer than
  reverse-engineering alone.
- One spare 9608 that can be reset and experimented on.

## 11. Open questions

1. How many phones and sites? This decides SQLite vs Postgres and single vs multi-tenant.
2. Are the phones currently on H.323 or SIP firmware, and which version? Do you have the SIP firmware (7.1.x)?
3. Which SIP trunk provider, if you've picked one?
4. Deployment: native install on the box (recommended), or Docker?
5. Should this server run DHCP for the phones, or will you set option 242 on your existing router?
6. Web stack OK? (FastAPI + React/TypeScript. The alternative is server-rendered HTML with htmx, which is simpler but less interactive.)
7. What button layout do you picture? A sketch of one or two typical phones helps (for example: 3 lines, then speed dials, then DND, forward and park).
8. Do any phones have (or will they get) a BM12 button module?
