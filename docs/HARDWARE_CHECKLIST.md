# 9608 hardware check (Phase 1)

Everything in Phase 1 has been tested with simulated phones. This checklist
confirms how a **real** 9608 behaves, so later phases build on facts rather than
forum posts. It takes about an hour with one or two phones.

Fill in the **Result** column and send it back (a photo of the phone's screen
helps when something fails).

## Before you start

1. Install PhoneSystem ([SETUP.md](SETUP.md)), create the admin account, and
   confirm the server IP under **Settings**.
2. Add two extensions (e.g. 101 and 102) and click **Apply changes**.
3. If a phone is still on H.323 firmware, upload the Avaya 96x1 SIP firmware zip
   under **Firmware**.
4. Set DHCP option 242 on the phones' network to the string shown on the
   Dashboard, e.g. `HTTPSRVR=192.168.1.10,SIG=2`.

> **Phone admin menu:** once a phone has loaded our settings, its local admin
> ("craft") menu password becomes the **Phone admin menu password** shown under
> Settings, instead of Avaya's default. To open the menu, press **Mute**, type the
> password, then press **#**.

## Checks

| # | Check | How | Result |
|---|---|---|---|
| 1 | Firmware before we start | Craft menu → VIEW, or the phone's About screen. Write down the version and whether it says H.323 or SIP | |
| 2 | Phone asks for its settings | Reboot the phone. Within a minute it should appear on **Phones** (or under "couldn't identify"). Are the requests all `200`? | |
| 3 | How the phone was identified | Open the phone on **Phones** and copy the **Device string**. Does it contain the MAC address? Is the phone on the same network/VLAN as the server (needed for automatic login)? | |
| 4 | H.323 → SIP conversion (skip if already SIP) | After step 2, does it download firmware and reboot into SIP? It can take 10+ minutes; don't unplug it. New firmware version? | |
| 5 | Phone logs | Does **Phone log** on the phone's page show messages? If not, turn on remote logging in the craft menu (LOG / syslog → server IP) and say which option names you saw | |
| 6 | Automatic login | Assign the phone to extension 101 and reboot it. Does it log in by itself, or does it ask for extension and password? Does the Dashboard show 101 as registered? | |
| 7 | Manual login (fallback) | If 6 failed: log in by hand with the extension and password shown under **Extensions → Edit**. Does it register? | |
| 8 | Basic call + caller name | Call 102 from 101. Does 102 show "101" and the name? Is audio fine both ways? | |
| 9 | Dial without pressing Call | Dial `102` and wait. Does it call straight away (no need to press Call/OK)? | |
| 10 | Echo test | Dial `*43`. Do you hear yourself? | |
| 11 | Hold / resume | During a call press Hold, then resume | |
| 12 | Blind transfer | Transfer a call from 101 to 102 without talking to 102 first | |
| 13 | Attended transfer | Call 102, talk, then complete the transfer | |
| 14 | 3-way conference | Conference a third party in | |
| 15 | Clock | Is the date/time right (time zone, daylight saving)? | |
| 16 | Buttons in standard mode | How many line (call appearance) buttons are there? Scroll to a blank button: is there a **Custom** / **Add** option for Autodial or Feature? | |
| 17 | Web browser | In the Home menu, is there a **Browser**/**Web** entry (it may be empty or greyed out)? | |
| 18 | Screen saver / backlight | Leave it idle. What does the screen show after a while? | |

## What happens next

- Checks 2–7 decide how phones are identified and log in; we adjust the settings
  file or the identification if needed.
- Checks 11–14 in standard mode are the baseline we must keep when Phase 2
  switches phones to AST mode for button remapping.
- Checks 16–18 shape Phase 2 (buttons) and Phase 3 (screen and phone apps).
  Phase 2 starts by capturing what the phone asks Avaya's PPM service for; that
  capture tool will come with its own short instructions.
