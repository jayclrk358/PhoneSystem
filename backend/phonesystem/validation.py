"""Validation shared by the API and the config generators.

Anything written into an Asterisk config file or the phone settings file goes
through one of these, so a stray newline or quote can never inject config.
"""

import re
import secrets
import string

EXTENSION_RE = re.compile(r"[1-9][0-9]{1,5}")
# Printable text without characters that have meaning in Asterisk configs
# (; [ ] " \ < >) or the phone settings file, and no control characters.
DISPLAY_NAME_RE = re.compile(r"[^\x00-\x1f\x7f;\[\]\"\\<>]{1,64}")
SIP_PASSWORD_RE = re.compile(r"[A-Za-z0-9]{8,13}")
MAC_RE = re.compile(r"[0-9a-f]{12}")

# The 96x1 SIP firmware accepts at most 13 characters in FORCE_SIP_PASSWORD.
SIP_PASSWORD_LENGTH = 13
_PASSWORD_ALPHABET = string.ascii_letters + string.digits


def generate_sip_password() -> str:
    # 13 alphanumeric characters is ~77 bits of entropy.
    return "".join(secrets.choice(_PASSWORD_ALPHABET) for _ in range(SIP_PASSWORD_LENGTH))


def check_extension_number(value: str) -> str:
    if not EXTENSION_RE.fullmatch(value):
        raise ValueError("extension must be 2-6 digits and not start with 0")
    return value


def check_display_name(value: str) -> str:
    value = value.strip()
    if not DISPLAY_NAME_RE.fullmatch(value):
        raise ValueError('name must be 1-64 characters, without ; [ ] " \\ < >')
    return value


def check_sip_password(value: str) -> str:
    if not SIP_PASSWORD_RE.fullmatch(value):
        raise ValueError("SIP password must be 8-13 letters or digits (9608 firmware limit)")
    return value


def normalize_mac(value: str) -> str:
    """Accept 00:1B:4F:12:34:56, 00-1b-4f-12-34-56, 001b.4f12.3456 or 001b4f123456."""
    mac = re.sub(r"[^0-9a-fA-F]", "", value).lower()
    if not MAC_RE.fullmatch(mac):
        raise ValueError("not a valid MAC address")
    return mac


def format_mac(mac: str) -> str:
    return ":".join(mac[i : i + 2] for i in range(0, 12, 2))
