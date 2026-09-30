# Setting up PhoneSystem

## What you need

- A Linux machine for the server. **Ubuntu 24.04 LTS** is the tested target
  (any Debian-based system with an Asterisk 20+ package should work). 2 CPU
  cores and 2 GB RAM are plenty for dozens of phones.
- A fixed IP address for that machine on the phones' network. The phones
  connect to it by IP address.
- Avaya 9608 / 9608G phones, powered by a PoE switch or power adapters.
- Access to the DHCP settings for the phones' network (usually your router).
- Avaya's **96x1 SIP** firmware zip (7.1.x), from Avaya support or a reseller.
  It's only needed if a phone still runs H.323 firmware, or to update firmware.

## 1. Install

```bash
git clone https://github.com/jayclrk358/PhoneSystem.git
cd PhoneSystem
sudo ./deploy/install.sh
```

The installer:
- installs Asterisk
- installs PhoneSystem into `/opt/phonesystem` and creates a `phonesystem` service user
- replaces Asterisk's config with PhoneSystem's (the original is kept in
  `/etc/asterisk.before-phonesystem`)
- makes a self-signed HTTPS certificate
- starts the `phonesystem` and `asterisk` services

Run it again later to upgrade; your data and settings are kept.

The web UI is built during install if Node.js 20+ is available. Otherwise build
it on any machine with `cd frontend && npm ci && npm run build` and copy
`frontend/dist` over before installing.

## 2. First login

Open `https://<server-ip>/`. Your browser will warn about the self-signed
certificate; accept it. Create the admin account, then work through
**Getting started** on the Dashboard:

1. **Settings:** confirm the server IP address (use **Use it** if the detected
   one is right).
2. **Extensions:** add one per person or desk. A SIP password is generated for
   each.
3. Click **Apply changes** in the yellow banner whenever it appears. Nothing
   reaches the phone system until you do.

## 3. Point the phones at the server (DHCP option 242)

Avaya phones find their settings server through DHCP option **242**. In your
router's DHCP settings, add a custom option:

- Option number: `242`
- Type: text / string
- Value: the string shown on the Dashboard or under Settings, e.g.
  `HTTPSRVR=192.168.1.10,SIG=2`

`SIG=2` tells phones that still run H.323 firmware to switch to SIP. If this
server should hand out addresses itself, see
`deploy/dnsmasq-phones.conf.example`. Never run two DHCP servers on one network.

## 4. Firmware

Under **Firmware**, upload Avaya's 96x1 SIP zip as-is. Its upgrade script is
then served to the phones, so they update (or convert from H.323) on their next
reboot. A conversion can take 10+ minutes; don't unplug the phone.

## 5. Phones

Reboot a phone. It appears under **Phones** the first time it asks for its
settings. Open it, pick an extension, and reboot it again: it logs in as that
extension automatically. You can also add phones ahead of time by the MAC
address on their label.

Automatic login needs the server and the phones on the same network segment
(VLAN): the server hands a phone its SIP password only after confirming the
phone's MAC address from its own ARP table.

## Ports

Allow these from the phones' network, and **don't expose them to the internet**:

| Port | Used for |
|---|---|
| 80/tcp | phones download settings and firmware |
| 443/tcp | web UI |
| 5060/tcp and udp | SIP (phones use TCP) |
| 10000–20000/udp | call audio |
| 514/udp | phone logs |

## Troubleshooting

- **Phone never shows up:** check option 242 and that the phone can reach port 80.
  Everything a phone requests is listed on the Dashboard and on the phone's
  page. A status other than `200` means a file was missing.
- **Phone shows up under "couldn't identify", or doesn't log in by itself:**
  the server confirms which phone is asking from its own ARP table, which only
  works when phones are on the same network segment (VLAN) as the server. Give
  the server an address on the phones' VLAN. Until then, log in on the phone
  with the extension and password shown under **Extensions → Edit**.
- **Phone doesn't register:** check the **Phone log** on the phone's page, and
  that extensions were applied (no yellow banner).
- **Server logs:** `journalctl -u phonesystem`, `/var/log/asterisk/messages.log`,
  and `sudo asterisk -rvvv` (then `pjsip set logger on` to watch SIP).
- **Something broke after a change:** **Config history** can roll Asterisk back to
  any earlier version.
- **Locked out of the web UI:** `sudo -u phonesystem env $(sudo grep -v '^#'
  /etc/phonesystem/phonesystem.env | xargs) phonesystem create-admin --username
  admin` resets the password.

## Backups

Copy `/var/lib/phonesystem` (database, generated configs, firmware) and
`/etc/phonesystem` (secrets, certificate). Backup and restore from the web UI
come in Phase 7.
