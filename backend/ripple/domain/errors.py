"""Domain exception hierarchy for the Ripple payment-fraud detection platform.

All domain-level errors inherit from RippleDomainError to allow unified
exception handling across payment rail ingestion adapters, graph normalization,
and detection pipelines.
"""

from typing import Any


class RippleDomainError(Exception):
    """Base domain exception for all domain model invariant violations in Ripple.

    Payment networks have widely differing operational invariants (e.g. instant settlement
    vs multi-day clearing, irreversible crypto vs chargeback-prone cards). When raw payment
    data fails to normalize into universal business rules, a RippleDomainError is raised.
    """

    def __init__(self, message: str, *args: Any) -> None:
        super().__init__(message, *args)
        self.message = message


class CurrencyMismatchError(RippleDomainError):
    """Raised when an operation is attempted between Money instances with differing currencies.

    Real-world payment rails (such as SWIFT cross-border wires or crypto on-ramps) require
    explicit foreign exchange (FX) conversion. Arithmetic or relational comparisons across
    un-normalized currency boundaries are prohibited to prevent silent valuation errors.
    """

    pass


class InvalidIdentifierError(RippleDomainError):
    """Raised when a PartyIdentifier violates the structural format rules for its type.

    Each payment network identifies counterparties using distinct structural formats:
    - UPI: Virtual Payment Address (username@bank)
    - SEPA/SWIFT: International Bank Account Number (IBAN ISO 13616) or BIC (ISO 9362)
    - Blockchains: Cryptographic public key hashes (e.g. 0x... for EVM, base58 for Bitcoin)
    - Wallets: E.164 phone numbers, RFC 5322 emails, or internal user IDs.
    Structural validation failures prevent malformed entity nodes from corrupting the graph.
    """

    pass


class InvalidTransactionError(RippleDomainError):
    """Raised when a Transaction violates universal lifecycle or state invariants.

    Common violations include:
    - Non-positive transaction amounts (zero or negative transfers).
    - Identity paradoxes (sender and receiver having identical party IDs).
    - Temporal inconsistencies (settled_at timestamp preceding initiated_at).
    - State inconsistencies (status is SETTLED but settled_at timestamp is missing).
    - Timezone violations (naive datetimes lacking explicit UTC specification).
    """

    pass


class PrecisionError(RippleDomainError):
    """Raised when monetary amounts violate decimal precision rules or use floating-point types.

    Financial systems cannot tolerate binary floating-point representation drift (IEEE 754).
    This error is raised when:
    - Float values are passed to monetary value objects.
    - An amount contains more fractional decimal places than permitted by the asset's scale.
    """

    pass
