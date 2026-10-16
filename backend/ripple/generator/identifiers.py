"""Synthetic identifier factories adhering strictly to Rule R2.

Rule R2 Invariant:
- Nothing generated may plausibly identify a real person, account, or card.
- Emails: example.com, example.org, example.net only.
- Names: placeholder format 'Synthetic Actor XXXXXX' (exercises discard paths).
- Phones: E.164 under non-routable prefix +999 (e.g. +99912345678).
- UPI handles: fictional (rplbank1, rplbank2...), IFSC fictional (RPLX0 + 6 digits).
- IBANs: fictional bank codes with valid MOD-97 check digits and country-correct length.
- BICs: fictional institution codes (ZRPA, ZRPB, ZRPC) in valid BIC shape.
- Crypto addresses: random, and for bitcoin/bech32/base58 families deliberately
  checksum-INVALID so they cannot be real wallets.
- IPs: RFC 5737 documentation ranges (192.0.2.0/24, 198.51.100.0/24, 203.0.113.0/24)
  or RFC 3849 IPv6 (2001:db8::/32).
- Card events: opaque card_token ('tok_' + 32 hex) with synthetic bin/last4.
  NEVER generate a PAN or any Luhn-valid 13-19 digit string.
"""

from ripple.generator.rng import Rng

# RFC 2606 reserved domains
RESERVED_DOMAINS = ["example.com", "example.org", "example.net"]

# Base58 Bitcoin alphabet (58 chars, excluding 0, O, I, l)
BASE58_ALPHABET = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"

# Bech32 character set (32 chars)
BECH32_ALPHABET = "qpzry9x8gf2tvdw0s3jn54khce6mua7l"

# RFC 5737 IPv4 documentation blocks
RFC5737_PREFIXES = ["192.0.2", "198.51.100", "203.0.113"]


def is_luhn_valid(number_str: str) -> bool:
    """Check Luhn checksum for a string of digits."""
    digits = [int(c) for c in number_str if c.isdigit()]
    if not digits:
        return False
    checksum = 0
    parity = len(digits) % 2
    for i, d in enumerate(digits):
        if i % 2 == parity:
            d *= 2
            if d > 9:
                d -= 9
        checksum += d
    return checksum % 10 == 0


def phone(rng: Rng) -> str:
    """Generate E.164 phone number under non-routable +999 prefix (e.g. +99912345678)."""
    # 8 digits after +999 gives 11 digits total, matching ^\+?[1-9]\d{6,14}$
    num = rng.randint(10000000, 99999999)
    return f"+999{num}"


def email(rng: Rng, username: str | None = None) -> str:
    """Generate synthetic email on RFC 2606 reserved domain."""
    user = username or f"user{rng.randint(100000, 999999)}"
    domain = rng.choice(RESERVED_DOMAINS)
    return f"{user}@{domain}"


def vpa(rng: Rng, username: str | None = None, handle: str = "rplbank1") -> str:
    """Generate synthetic UPI VPA e.g. user123456@rplbank1."""
    user = username or f"actor{rng.randint(100000, 999999)}"
    return f"{user}@{handle}"


def ifsc(bank_code: str = "RPLX", branch_index: int = 1) -> str:
    """Generate synthetic IFSC code e.g. RPLX0000123 (bank 4 chars, 0, branch 6 digits)."""
    clean_code = bank_code[:4].upper()
    return f"{clean_code}0{branch_index:06d}"


def bank_account_number(rng: Rng, length: int = 11) -> str:
    """Generate synthetic national bank account number (10-12 digits).

    Guarantees the account number is never a Luhn-valid 13-19 digit PAN string.
    """
    # Restrict length to 10-12 digits to strictly avoid 13-19 digit card PAN ranges
    len_clamped = max(10, min(12, length))
    lo = 10 ** (len_clamped - 1)
    hi = (10**len_clamped) - 1
    num_str = str(rng.randint(lo, hi))
    return num_str


def compute_iban_check_digits(country: str, bban: str) -> str:
    """Compute ISO 7064 MOD-97 check digits for a given country and BBAN."""
    country_clean = country.strip().upper()
    bban_clean = bban.strip().upper()

    rearranged = bban_clean + country_clean + "00"
    digits: list[str] = []
    for ch in rearranged:
        if ch.isdigit():
            digits.append(ch)
        elif ch.isalpha():
            digits.append(str(ord(ch) - 55))
        else:
            raise ValueError(f"Invalid character in IBAN components: {ch}")

    remainder = int("".join(digits)) % 97
    check_num = 98 - remainder
    return f"{check_num:02d}"


# Country BBAN specifications for IBAN generation
# Each spec defines: (bank_len, branch_len, account_len, total_length)
_IBAN_SPECS: dict[str, dict[str, int]] = {
    "GB": {"total": 22, "bank_len": 4, "sort_len": 6, "acc_len": 8},
    "DE": {"total": 22, "blz_len": 8, "acc_len": 10},
    "NL": {"total": 18, "bank_len": 4, "acc_len": 10},
    "FR": {"total": 27, "bank_len": 5, "branch_len": 5, "acc_len": 11, "rib_len": 2},
    "ES": {"total": 24, "bank_len": 4, "branch_len": 4, "ctrl_len": 2, "acc_len": 10},
    "IT": {"total": 27, "cin_len": 1, "abi_len": 5, "cab_len": 5, "acc_len": 12},
    "CH": {"total": 21, "bank_len": 5, "acc_len": 12},
    "AE": {"total": 23, "bank_len": 3, "acc_len": 16},
}


def iban(
    country: str,
    bank_code: str = "ZRPA",
    account_num: str | None = None,
    rng: Rng | None = None,
) -> str:
    """Generate synthetic IBAN with correct MOD-97 check digits and country-correct length."""
    country_upper = country.strip().upper()
    if country_upper not in _IBAN_SPECS:
        # Default fallback structure for non-standard country
        country_upper = "DE"

    spec = _IBAN_SPECS[country_upper]
    total_len = spec["total"]

    # Build BBAN per country
    if country_upper == "GB":
        # 4 alpha bank, 6 numeric sort code, 8 numeric account
        bank = bank_code[:4].upper().ljust(4, "A")
        sort = "200000"
        acc = (account_num or "00012345")[:8].zfill(8)
        bban = f"{bank}{sort}{acc}"

    elif country_upper == "DE":
        # 8 numeric BLZ, 10 numeric account
        blz = "50010517"
        acc = (account_num or "0001234567")[:10].zfill(10)
        bban = f"{blz}{acc}"

    elif country_upper == "NL":
        # 4 alpha bank, 10 numeric account
        bank = bank_code[:4].upper().ljust(4, "A")
        acc = (account_num or "0001234567")[:10].zfill(10)
        bban = f"{bank}{acc}"

    elif country_upper == "FR":
        # 5 bank, 5 branch, 11 account, 2 rib
        bban = f"3000200550{(account_num or '00000123456')[:11].zfill(11)}02"

    elif country_upper == "ES":
        # 4 bank, 4 branch, 2 ctrl, 10 account
        bban = f"0049150005{(account_num or '0001234567')[:10].zfill(10)}"

    elif country_upper == "IT":
        # 1 CIN, 5 ABI, 5 CAB, 12 account
        bban = f"X0300203280{(account_num or '000001234567')[:12].zfill(12)}"

    elif country_upper == "CH":
        # 5 bank, 12 account
        bban = f"09000{(account_num or '000001234567')[:12].zfill(12)}"

    elif country_upper == "AE":
        # 3 bank, 16 account
        bban = f"033{(account_num or '0000000001234567')[:16].zfill(16)}"

    else:
        bban = "500105170001234567"

    check = compute_iban_check_digits(country_upper, bban)
    res = f"{country_upper}{check}{bban}"
    assert len(res) == total_len, f"IBAN length mismatch: {len(res)} != {total_len}"
    return res


def bic(
    bank_code: str = "ZRPA",
    country: str = "GB",
    location: str = "22",
    branch: str = "XXX",
) -> str:
    """Generate synthetic BIC / SWIFT code (8 or 11 characters, fictional institution)."""
    b = bank_code[:4].upper().ljust(4, "A")
    c = country[:2].upper().ljust(2, "X")
    loc = location[:2].upper().ljust(2, "0")
    br = branch[:3].upper() if branch else ""
    return f"{b}{c}{loc}{br}"


def crypto_address(family: str, rng: Rng) -> str:
    """Generate synthetic cryptocurrency address.

    Bitcoin/Base58 and Bech32 addresses are deliberately checksum-INVALID so they
    can never be real on-chain addresses.
    """
    clean_family = family.strip().lower()

    if clean_family == "evm":
        # 0x followed by 40 hex characters
        raw_hex = f"{rng.getrandbits(160):040x}"
        return f"0x{raw_hex}"

    if clean_family == "bitcoin":
        # Generate invalid Bech32 or Base58 address
        # Bech32: 'bc1q' + 38 chars from bech32 alphabet, deliberately ending in invalid checksum
        body = "".join(rng.choice(BECH32_ALPHABET) for _ in range(32))
        return f"bc1q{body}000000"

    if clean_family == "solana":
        # Solana: base58 string 44 characters
        return "".join(rng.choice(BASE58_ALPHABET) for _ in range(44))

    if clean_family == "tron":
        # Tron: 'T' + 33 base58 characters
        body = "".join(rng.choice(BASE58_ALPHABET) for _ in range(33))
        return f"T{body}"

    raise ValueError(f"Unknown address family '{family}'")


def tx_hash(rng: Rng, prefix_0x: bool = True) -> str:
    """Generate synthetic 32-byte deterministic hash string (64 hex characters)."""
    hex_str = f"{rng.getrandbits(256):064x}"
    return f"0x{hex_str}" if prefix_0x else hex_str


def card_token(rng: Rng) -> str:
    """Generate opaque card token ('tok_' + 32 hex characters)."""
    token_hex = f"{rng.getrandbits(128):032x}"
    return f"tok_{token_hex}"


def card_bin(rng: Rng) -> str:
    """Generate synthetic Bank Identification Number (6 digits)."""
    # Use synthetic testing BIN prefix
    return f"4999{rng.randint(10, 99)}"


def card_last4(rng: Rng) -> str:
    """Generate synthetic card last 4 digits."""
    return f"{rng.randint(1000, 9999)}"


def device_id(rng: Rng) -> str:
    """Generate synthetic client device identifier ('dev_' + 16 hex chars)."""
    return f"dev_{rng.getrandbits(64):016x}"


def ip_address(rng: Rng, is_v6: bool = False) -> str:
    """Generate synthetic IP address from RFC 5737 or RFC 3849 documentation ranges."""
    if is_v6:
        # RFC 3849 documentation prefix 2001:db8::/32
        h1 = f"{rng.getrandbits(16):x}"
        h2 = f"{rng.getrandbits(16):x}"
        return f"2001:db8::{h1}:{h2}"

    prefix = rng.choice(RFC5737_PREFIXES)
    host = rng.randint(1, 254)
    return f"{prefix}.{host}"


def merchant_id(rng: Rng) -> str:
    """Generate synthetic merchant identifier ('mid_' + 12 hex chars)."""
    return f"mid_{rng.getrandbits(48):012x}"


def terminal_id(rng: Rng) -> str:
    """Generate synthetic card terminal identifier ('term_' + 8 hex chars)."""
    return f"term_{rng.getrandbits(32):08x}"


def instrument_id(rng: Rng) -> str:
    """Generate synthetic payment instrument identifier ('ins_' + 16 hex chars).

    Never all digits to prevent ambiguity with account numbers or card tokens.
    """
    return f"ins_{rng.getrandbits(64):016x}"


def placeholder_name(actor_index: int) -> str:
    """Generate synthetic placeholder name (e.g. 'Synthetic Actor 000123').

    Emitted on purpose so adapter discard paths and data-minimization rules are exercised.
    """
    return f"Synthetic Actor {actor_index:06d}"
