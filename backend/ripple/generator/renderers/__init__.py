"""Pure renderers package for payment network adapters."""

from ripple.generator.plan import register_renderer
from ripple.generator.renderers.card import render_card_payload
from ripple.generator.renderers.crypto import render_crypto_payload
from ripple.generator.renderers.swift import render_swift_payload
from ripple.generator.renderers.upi import render_upi_payload
from ripple.generator.renderers.wallet import render_wallet_payload


def register_all_renderers() -> None:
    """Register all 5 built-in pure renderers with the plan registry."""
    register_renderer("upi", render_upi_payload)
    register_renderer("card", render_card_payload)
    register_renderer("crypto", render_crypto_payload)
    register_renderer("swift", render_swift_payload)
    register_renderer("wallet", render_wallet_payload)


# Automatically register on import
register_all_renderers()

__all__ = [
    "register_all_renderers",
    "render_card_payload",
    "render_crypto_payload",
    "render_swift_payload",
    "render_upi_payload",
    "render_wallet_payload",
]
