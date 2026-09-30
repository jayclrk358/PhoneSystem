"""Work out which phone (MAC address) sent a provisioning request."""

import re
from pathlib import Path

from ...validation import normalize_mac

# e.g. "... MAC:00:1b:4f:12:34:56 ..." or "(MAC=001b4f123456)"
_UA_MAC = re.compile(r"MAC\s*[:=]?\s*((?:[0-9A-Fa-f]{2}[:-]?){5}[0-9A-Fa-f]{2})", re.IGNORECASE)
_UA_MODEL = re.compile(r"\b(96[0-9]{2}G?S?|J1[0-9]{2})\b")
_UA_FIRMWARE = re.compile(r"\b(?:S?96x1|SIP)[-_A-Za-z]*[-_]?R?([0-9]+(?:[._][0-9]+){1,4})")

ARP_TABLE = Path("/proc/net/arp")


def mac_from_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    m = _UA_MAC.search(user_agent)
    if not m:
        return None
    try:
        return normalize_mac(m.group(1))
    except ValueError:
        return None


def mac_from_arp(ip: str, arp_table: Path = ARP_TABLE) -> str | None:
    """Look up ``ip`` in the kernel's ARP table.

    Only hosts on a directly connected network appear there under their own
    IP, so a phone behind a router is never mistaken for the router (we look
    up the phone's IP, which won't be in the table).
    """
    try:
        lines = arp_table.read_text().splitlines()[1:]
    except OSError:
        return None
    for line in lines:
        parts = line.split()
        if len(parts) < 4 or parts[0] != ip:
            continue
        flags, hw = parts[2], parts[3]
        if flags == "0x0" or hw == "00:00:00:00:00:00":
            return None  # incomplete entry
        try:
            return normalize_mac(hw)
        except ValueError:
            return None
    return None


def model_from_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    m = _UA_MODEL.search(user_agent)
    return m.group(1) if m else None


def firmware_from_user_agent(user_agent: str | None) -> str | None:
    if not user_agent:
        return None
    m = _UA_FIRMWARE.search(user_agent)
    return m.group(1).replace("_", ".") if m else None
