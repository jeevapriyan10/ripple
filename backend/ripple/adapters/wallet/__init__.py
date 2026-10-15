"""Closed-Loop Payment Wallet network adapter package."""

from ripple.adapters.wallet.adapter import WalletAdapter
from ripple.adapters.wallet.profile import WALLET_PROFILE

__all__ = [
    "WALLET_PROFILE",
    "WalletAdapter",
]
