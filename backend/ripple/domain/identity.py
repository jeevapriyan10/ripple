"""Party identity abstractions across disparate payment rails.

Different payment networks identify counterparties using completely incompatible
addressing schemes:
- UPI: Virtual Payment Addresses (VPAs e.g. alice@oksbi) or phone numbers.
- Card Networks: Tokenized Primary Account Numbers (PANs) and Merchant IDs.
- Cryptocurrency: Cryptographic addresses (Base58 or hexadecimal public key hashes).
- SWIFT / SEPA: International Bank Account Numbers (IBAN) and Bank Identifier Codes (BIC).
- Closed-Loop Wallets: Email addresses, phone numbers, or proprietary UUIDs.

This module unifies them into a canonical `PartyIdentifier` model with structural
format validation and deduplicating canonical normalization.
"""

import re
from enum import Enum
from typing import Any, Self

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ripple.domain.errors import InvalidIdentifierError


class IdentifierType(str, Enum):
    """Enumeration of counterparty identifier types across global payment rails.

    VPA: Virtual Payment Address (e.g. user@bank in India's UPI ecosystem).
    PHONE: E.164 structured telephone number (P2P wallets, UPI, Pix).
    BANK_ACCOUNT: National clearing account number (ACH, Fedwire, BACS, NEFT).
    IBAN: International Bank Account Number (SEPA, European, and Middle Eastern banking).
    BIC: Bank Identifier Code / SWIFT code (ISO 9362 for financial institution routing).
    CARD_PAN_TOKEN: Tokenized card primary account number (Visa, Mastercard, Apple Pay).
    CRYPTO_ADDRESS: Public key hash / wallet address (Bitcoin, Ethereum, Solana).
    WALLET_USER_ID: Proprietary user identifier within closed-loop systems (PayPal, Venmo).
    EMAIL: RFC 5322 electronic mail address (PayPal, Zelle).
    MERCHANT_ID: Terminal or merchant identifier in card acquirer ecosystems.
    OTHER: Fallback for emerging or private network addressing schemas.
    """

    VPA = "VPA"
    PHONE = "PHONE"
    BANK_ACCOUNT = "BANK_ACCOUNT"
    IBAN = "IBAN"
    BIC = "BIC"
    CARD_PAN_TOKEN = "CARD_PAN_TOKEN"
    CRYPTO_ADDRESS = "CRYPTO_ADDRESS"
    WALLET_USER_ID = "WALLET_USER_ID"
    EMAIL = "EMAIL"
    MERCHANT_ID = "MERCHANT_ID"
    OTHER = "OTHER"


# Pre-compiled regex patterns for structural validation
_VPA_PATTERN = re.compile(r"^[a-zA-Z0-9.\-_]{1,64}@[a-zA-Z0-9.\-_]{2,64}$")
_EMAIL_PATTERN = re.compile(r"^[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+$")
_BIC_PATTERN = re.compile(r"^[A-Z]{4}[A-Z]{2}[A-Z0-9]{2}([A-Z0-9]{3})?$")
_PHONE_PATTERN = re.compile(r"^\+?[1-9]\d{6,14}$")
_IBAN_PATTERN = re.compile(r"^[A-Z]{2}\d{2}[A-Z0-9]{10,30}$")


def _is_valid_iban(iban: str) -> bool:
    """Validate IBAN structure and ISO 7064 MOD-97-10 checksum."""
    cleaned = iban.replace(" ", "").upper()
    if not _IBAN_PATTERN.match(cleaned):
        return False
    # Rearrange: first 4 characters move to the end
    reordered = cleaned[4:] + cleaned[:4]
    # Replace letters with two-digit equivalents (A=10, ..., Z=35)
    digits = []
    for ch in reordered:
        if ch.isdigit():
            digits.append(ch)
        elif ch.isalpha():
            digits.append(str(ord(ch) - 55))
        else:
            return False
    digits_str = "".join(digits)
    try:
        return int(digits_str) % 97 == 1
    except ValueError:
        return False


class PartyIdentifier(BaseModel):
    """Immutable representation of a counterparty address or account on a specific rail.

    A single real-world entity (e.g. an individual or merchant) typically holds multiple
    identifiers across differing rails (e.g. an IBAN for wire transfers, an Ethereum address
    for Web3, a VPA for UPI). `PartyIdentifier` allows normalized entity resolution.
    """

    model_config = ConfigDict(frozen=True)

    type: IdentifierType = Field(
        ...,
        description="The structural type of the identifier (e.g. VPA, IBAN, CRYPTO_ADDRESS).",
    )
    value: str = Field(
        ...,
        description="The raw string address or account token.",
    )
    network: str = Field(
        ...,
        description="Payment network (e.g. 'UPI', 'SEPA', 'ETHEREUM', 'VISA').",
    )
    is_pseudonymous: bool = Field(
        default=False,
        description="True for privacy-preserving or un-hosted addresses (crypto, tokenized PANs).",
    )

    def __init__(self, **data: Any) -> None:
        super().__init__(**data)

    @model_validator(mode="after")
    def _validate_format(self) -> Self:
        val = self.value.strip()
        if not val:
            raise InvalidIdentifierError(f"Identifier value for {self.type.value} cannot be empty.")

        match self.type:
            case IdentifierType.VPA:
                if not _VPA_PATTERN.match(val):
                    raise InvalidIdentifierError(
                        f"Malformed VPA '{val}'. Expected format 'username@handle'."
                    )

            case IdentifierType.PHONE:
                # Strip spaces, hyphens, and parentheses for structural check
                cleaned_phone = re.sub(r"[\s\-\(\)]", "", val)
                if not _PHONE_PATTERN.match(cleaned_phone):
                    raise InvalidIdentifierError(
                        f"Malformed phone number '{val}'. "
                        "Expected E.164 standard (7-15 digits, optional leading +)."
                    )

            case IdentifierType.IBAN:
                if not _is_valid_iban(val):
                    raise InvalidIdentifierError(
                        f"Invalid IBAN '{val}'. Fails ISO 13616 structure or MOD-97 checksum."
                    )

            case IdentifierType.BIC:
                cleaned_bic = val.replace(" ", "").upper()
                if not _BIC_PATTERN.match(cleaned_bic):
                    raise InvalidIdentifierError(
                        f"Invalid BIC/SWIFT code '{val}'. "
                        "Must be 8 or 11 alphanumeric characters (ISO 9362)."
                    )

            case IdentifierType.CRYPTO_ADDRESS:
                if any(c.isspace() for c in val):
                    raise InvalidIdentifierError(
                        f"Crypto address '{val}' cannot contain whitespace."
                    )
                if len(val) < 20 or len(val) > 128:
                    raise InvalidIdentifierError(
                        f"Crypto address '{val}' length ({len(val)}) "
                        "must be between 20 and 128 characters."
                    )

            case IdentifierType.EMAIL:
                if not _EMAIL_PATTERN.match(val):
                    raise InvalidIdentifierError(f"Malformed email address '{val}'.")

            case IdentifierType.CARD_PAN_TOKEN:
                if any(c.isspace() for c in val):
                    raise InvalidIdentifierError(
                        f"Card PAN token '{val}' cannot contain whitespace."
                    )
                if len(val) < 12:
                    raise InvalidIdentifierError(
                        f"Card PAN token '{val}' is too short (min 12 characters)."
                    )

            case IdentifierType.BANK_ACCOUNT:
                cleaned_acc = val.replace(" ", "")
                if len(cleaned_acc) < 4:
                    raise InvalidIdentifierError(
                        f"Bank account '{val}' is too short (min 4 characters)."
                    )

            case IdentifierType.WALLET_USER_ID:
                if any(c.isspace() for c in val):
                    raise InvalidIdentifierError(
                        f"Wallet user ID '{val}' cannot contain whitespace."
                    )

            case IdentifierType.MERCHANT_ID:
                if not val:
                    raise InvalidIdentifierError("Merchant ID cannot be empty.")

            case IdentifierType.OTHER:
                if not val:
                    raise InvalidIdentifierError("Identifier value cannot be empty.")

        return self

    def normalize(self) -> str:
        """Produce a canonical string form used for entity deduplication and graph indexing.

        Canonical rules:
        - EMAIL, VPA: Trimmed and lowercased.
        - IBAN, BIC: Trimmed, spaces removed, uppercased.
        - PHONE: Strip formatting chars (dashes, spaces, parentheses).
        - CRYPTO_ADDRESS: Trimmed; 0x-prefixed hex and bech32 (bc1) addresses lowercased;
          case-sensitive base58 (Bitcoin legacy, Solana, Tron) preserved exactly.
        - Other: Trimmed.
        """
        raw = self.value.strip()
        match self.type:
            case IdentifierType.EMAIL | IdentifierType.VPA:
                return raw.lower()
            case IdentifierType.IBAN | IdentifierType.BIC:
                return raw.replace(" ", "").upper()
            case IdentifierType.PHONE:
                has_plus = raw.startswith("+")
                digits_only = re.sub(r"\D", "", raw)
                return f"+{digits_only}" if has_plus else digits_only
            case IdentifierType.CRYPTO_ADDRESS:
                if raw.startswith(("0x", "0X")) or raw.lower().startswith("bc1"):
                    return raw.lower()
                return raw
            case _:
                return raw

    def to_canonical_uri(self) -> str:
        """Generate a universally unique URI combining rail, type, and normalized value."""
        return f"{self.network.lower()}:{self.type.value.lower()}:{self.normalize()}"
