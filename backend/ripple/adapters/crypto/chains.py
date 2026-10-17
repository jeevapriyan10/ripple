"""Cryptocurrency blockchain specifications and simulation defaults.

Provides configuration and conventional thresholds for supported blockchain networks.
NOTE: Confirmation thresholds defined here are conventional defaults for simulation,
are NOT authoritative, and must be verified against specific protocol requirements
before use in production or real-money environments.
"""

from typing import NamedTuple

from ripple.adapters.errors import NormalizationError


class ChainSpec(NamedTuple):
    """Specification of a blockchain's addressing family, native asset, and confirmation rules."""

    family: str  # "evm", "bitcoin", "solana", "tron"
    native_symbol: str
    decimals: int
    required_confirmations: int
    description: str


# Conventional simulation defaults for well-known chains
DEFAULT_CHAINS: dict[str, ChainSpec] = {
    "bitcoin": ChainSpec(
        family="bitcoin",
        native_symbol="BTC",
        decimals=8,
        required_confirmations=6,
        description="Bitcoin UTXO rail (simulation default: 6 block confirmations, ~60m).",
    ),
    "ethereum": ChainSpec(
        family="evm",
        native_symbol="ETH",
        decimals=18,
        required_confirmations=64,
        description=(
            "Ethereum PoS / EVM rail (simulation default: 2 finalized epochs / 64 slots, ~13m)."
        ),
    ),
    "solana": ChainSpec(
        family="solana",
        native_symbol="SOL",
        decimals=9,
        required_confirmations=32,
        description="Solana PoH rail (simulation default: 32 confirmed slots, ~13s).",
    ),
    "tron": ChainSpec(
        family="tron",
        native_symbol="TRX",
        decimals=6,
        required_confirmations=19,
        description="Tron DPoS rail (simulation default: 19 SR confirmed blocks, ~57s).",
    ),
}

SUPPORTED_ADDRESS_FAMILIES = frozenset({"evm", "bitcoin", "solana", "tron"})


def resolve_chain_spec(
    chain_name: str,
    payload_family: str | None = None,
    payload_required_confirmations: int | None = None,
) -> ChainSpec:
    """Resolve chain specification from defaults or explicit payload parameters.

    If chain_name is unrecognized, the payload MUST explicitly supply BOTH
    address_family (one of: evm, bitcoin, solana, tron) and required_confirmations.
    Otherwise, raises NormalizationError listing the known chains.
    """
    clean_chain = str(chain_name).strip().lower()
    if clean_chain in DEFAULT_CHAINS:
        return DEFAULT_CHAINS[clean_chain]

    # Custom / unlisted chain
    if payload_family is not None and payload_required_confirmations is not None:
        clean_family = str(payload_family).strip().lower()
        if clean_family not in SUPPORTED_ADDRESS_FAMILIES:
            raise NormalizationError(
                f"Unsupported address_family '{payload_family}' for custom chain '{chain_name}'. "
                f"Allowed address families: {sorted(SUPPORTED_ADDRESS_FAMILIES)}",
                field="address_family",
                reason=f"Unsupported address family '{payload_family}'",
            )
        if payload_required_confirmations < 1:
            raise NormalizationError(
                "Field 'required_confirmations' must be an integer >= 1.",
                field="required_confirmations",
                reason="Invalid required_confirmations",
            )
        return ChainSpec(
            family=clean_family,
            native_symbol=clean_chain.upper(),
            decimals=18 if clean_family == "evm" else 8,
            required_confirmations=payload_required_confirmations,
            description=f"Custom {clean_family} chain '{chain_name}'.",
        )

    families = sorted(SUPPORTED_ADDRESS_FAMILIES)
    known_list = sorted(DEFAULT_CHAINS.keys())
    raise NormalizationError(
        f"Unknown blockchain '{chain_name}'. For custom chains, payload must supply both "
        f"'address_family' (one of {families}) and 'required_confirmations'. "
        f"Known chains: {known_list}",
        field="chain",
        reason=f"Unknown chain '{chain_name}'",
    )
