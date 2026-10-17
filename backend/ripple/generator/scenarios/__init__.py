"""Registry and catalog for all 10 synthetic fraud and laundering scenarios."""

from ripple.generator.scenarios.account_takeover import AccountTakeoverDrainScenario
from ripple.generator.scenarios.base import (
    Scenario,
    ScenarioContext,
    ScenarioPlan,
    estimate_scenario_rates,
    get_scenario,
    get_scenario_catalog,
    list_scenarios,
    register_scenario,
)
from ripple.generator.scenarios.card_testing import CardTestingScenario
from ripple.generator.scenarios.chargeback_fraud import ChargebackFraudMerchantScenario
from ripple.generator.scenarios.coordinator import ScenarioCoordinator
from ripple.generator.scenarios.cross_network import CrossNetworkLaunderingScenario
from ripple.generator.scenarios.crypto_peeling import CryptoPeelingLayeringScenario
from ripple.generator.scenarios.fan_in_smurfing import FanInSmurfingScenario
from ripple.generator.scenarios.nft_wash_trading import NftWashTradingScenario
from ripple.generator.scenarios.swift_structuring import SwiftStructuringLayeringScenario
from ripple.generator.scenarios.upi_mule_chain import UpiMuleChainScenario
from ripple.generator.scenarios.wallet_funding_abuse import WalletProvisionalFundingAbuseScenario

__all__ = [
    "AccountTakeoverDrainScenario",
    "CardTestingScenario",
    "ChargebackFraudMerchantScenario",
    "CrossNetworkLaunderingScenario",
    "CryptoPeelingLayeringScenario",
    "FanInSmurfingScenario",
    "NftWashTradingScenario",
    "Scenario",
    "ScenarioContext",
    "ScenarioCoordinator",
    "ScenarioPlan",
    "SwiftStructuringLayeringScenario",
    "UpiMuleChainScenario",
    "WalletProvisionalFundingAbuseScenario",
    "estimate_scenario_rates",
    "get_scenario",
    "get_scenario_catalog",
    "list_scenarios",
    "register_scenario",
]
