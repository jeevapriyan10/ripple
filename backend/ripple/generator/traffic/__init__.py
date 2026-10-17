"""Background traffic planners and hard-negative sources for Ripple generator."""

from ripple.generator.engine import register_source
from ripple.generator.traffic.card import CardTrafficPlanner
from ripple.generator.traffic.crypto import CryptoTrafficPlanner
from ripple.generator.traffic.hard_negatives import HardNegativePlanner
from ripple.generator.traffic.swift import SwiftTrafficPlanner
from ripple.generator.traffic.upi import UpiTrafficPlanner
from ripple.generator.traffic.wallet import WalletTrafficPlanner


def register_all_traffic_planners() -> None:
    """Register all 5 network background traffic planners plus the hard-negative planner."""
    register_source(UpiTrafficPlanner())
    register_source(CardTrafficPlanner())
    register_source(CryptoTrafficPlanner())
    register_source(SwiftTrafficPlanner())
    register_source(WalletTrafficPlanner())
    register_source(HardNegativePlanner())


# Automatically register planners when imported
register_all_traffic_planners()

__all__ = [
    "CardTrafficPlanner",
    "CryptoTrafficPlanner",
    "HardNegativePlanner",
    "SwiftTrafficPlanner",
    "UpiTrafficPlanner",
    "WalletTrafficPlanner",
    "register_all_traffic_planners",
]
