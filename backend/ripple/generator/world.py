"""World model, economic institutions, synthetic actors, and demographic pools.

Rule R1 & R2 Invariants:
- Determinism and independence: every actor's identities across all networks are drawn
  from isolated substreams 'world/actor/<index>/<network>' for ALL networks regardless of
  config.networks. Toggling one network does not change another network's identities.
- Synthetic-identifier safety: all identifiers produced satisfy Rule R2.
- No float in stored or rendered money.
"""

from datetime import datetime, timedelta
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from ripple.generator.calibration import CalibrationProfile
from ripple.generator.config import GeneratorConfig
from ripple.generator.identifiers import (
    bank_account_number,
    bic,
    card_bin,
    card_last4,
    card_token,
    crypto_address,
    email,
    iban,
    ifsc,
    instrument_id,
    merchant_id,
    phone,
    placeholder_name,
    terminal_id,
    vpa,
)
from ripple.generator.rng import RngFactory

# Supported countries with standard fiat currencies and primary IANA timezones
COUNTRY_SPECS: dict[str, dict[str, str]] = {
    "IN": {"currency": "INR", "timezone": "Asia/Kolkata", "bban_format": "account"},
    "US": {"currency": "USD", "timezone": "America/New_York", "bban_format": "account"},
    "GB": {"currency": "GBP", "timezone": "Europe/London", "bban_format": "iban"},
    "DE": {"currency": "EUR", "timezone": "Europe/Berlin", "bban_format": "iban"},
    "FR": {"currency": "EUR", "timezone": "Europe/Paris", "bban_format": "iban"},
    "NL": {"currency": "EUR", "timezone": "Europe/Amsterdam", "bban_format": "iban"},
    "AE": {"currency": "AED", "timezone": "Asia/Dubai", "bban_format": "iban"},
    "SG": {"currency": "SGD", "timezone": "Asia/Singapore", "bban_format": "account"},
    "HK": {"currency": "HKD", "timezone": "Asia/Hong_Kong", "bban_format": "account"},
    "BR": {"currency": "BRL", "timezone": "America/Sao_Paulo", "bban_format": "account"},
    "ZA": {"currency": "ZAR", "timezone": "Africa/Johannesburg", "bban_format": "account"},
    "JP": {"currency": "JPY", "timezone": "Asia/Tokyo", "bban_format": "account"},
}

MERCHANT_MCCS: list[str] = [
    "5411",  # Grocery stores, supermarkets
    "5812",  # Eating places, restaurants
    "5311",  # Department stores
    "5999",  # Miscellaneous retail
    "5732",  # Electronic sales
    "5651",  # Family clothing
    "4121",  # Taxicabs and limousines
    "5541",  # Service stations (fuel)
    "7999",  # Recreation services
    "7011",  # Hotels and motels
]


class Institution(BaseModel):
    """Simulated financial institution (bank, acquirer, issuer, or switch)."""

    model_config = ConfigDict(frozen=True)

    institution_id: str = Field(..., description="Institutional code e.g. 'I01'")
    name: str = Field(..., description="Fictional entity name")
    country: str = Field(..., description="Country code (ISO-3166 alpha-2)")
    bic: str = Field(..., description="Fictional BIC/SWIFT code")
    upi_handle: str = Field(..., description="UPI PSP handle (e.g. 'rplbank1')")
    ifsc_prefix: str = Field(..., description="IFSC 4-character prefix (e.g. 'RPLX')")
    issuer_id: str = Field(..., description="Card issuer identification code")
    acquirer_id: str = Field(..., description="Card acquirer identification code")
    size_weight: float = Field(default=1.0, description="Relative market share weight")


class Actor(BaseModel):
    """Simulated economic actor (individual, merchant, business, exchange, or service)."""

    model_config = ConfigDict(frozen=True)

    actor_id: str = Field(..., description="Unique actor identifier e.g. 'A000001'")
    actor_index: int = Field(..., description="0-indexed actor sequence number")
    name: str = Field(..., description="Placeholder synthetic name")
    kind: str = Field(..., description="individual|merchant|business|exchange|psp|service|bank")
    country: str = Field(..., description="Primary ISO-3166 alpha-2 country")
    timezone: str = Field(..., description="Local IANA timezone")
    institution_id: str = Field(..., description="Primary banking institution ID")
    created_at: datetime = Field(..., description="Account origination timestamp in UTC")
    activity_multiplier: float = Field(
        default=1.0, description="Heavy-tailed relative activity rate"
    )
    mcc: str | None = Field(default=None, description="Merchant Category Code if commercial seller")

    # Shared contact points
    shared_phone: str | None = Field(default=None)
    shared_email: str | None = Field(default=None)

    # Rail-specific identities
    upi_identities: dict[str, Any] = Field(default_factory=dict)
    card_identities: dict[str, Any] = Field(default_factory=dict)
    crypto_identities: dict[str, Any] = Field(default_factory=dict)
    swift_identities: dict[str, Any] = Field(default_factory=dict)
    wallet_identities: dict[str, Any] = Field(default_factory=dict)

    @property
    def network_identities(self) -> dict[str, dict[str, Any]]:
        """Return mapping of network name to identity dictionary."""
        return {
            "upi": self.upi_identities,
            "card": self.card_identities,
            "crypto": self.crypto_identities,
            "swift": self.swift_identities,
            "wallet": self.wallet_identities,
        }

    def has_network(self, network: str) -> bool:
        """Check if actor participates on the specified network."""
        net = network.lower()
        if net == "upi":
            return bool(self.upi_identities)
        if net == "card":
            return bool(self.card_identities)
        if net == "crypto":
            return bool(self.crypto_identities)
        if net == "swift":
            return bool(self.swift_identities)
        if net == "wallet":
            return bool(self.wallet_identities)
        return False


class World:
    """Simulated world container providing indexed lookups and actor pools."""

    def __init__(
        self,
        config: GeneratorConfig,
        institutions: list[Institution],
        actors: list[Actor],
        mule_pool: list[Actor],
    ) -> None:
        self.config = config
        self.institutions = institutions
        self.actors = actors
        self.mule_pool = mule_pool

        self._actors_by_id: dict[str, Actor] = {a.actor_id: a for a in actors}
        self._institutions_by_id: dict[str, Institution] = {
            i.institution_id: i for i in institutions
        }

        # Index actors by network
        self._actors_by_network: dict[str, list[Actor]] = {
            net: [a for a in actors if a.has_network(net)]
            for net in ["upi", "card", "crypto", "swift", "wallet"]
        }

        # Index merchants by country
        self._merchants_by_country: dict[str, list[Actor]] = {}
        for a in actors:
            if a.kind == "merchant":
                self._merchants_by_country.setdefault(a.country, []).append(a)

    def get_actor(self, actor_id: str) -> Actor:
        """Look up actor by ID, raising KeyError if absent."""
        return self._actors_by_id[actor_id]

    def get_institution(self, institution_id: str) -> Institution:
        """Look up institution by ID, raising KeyError if absent."""
        return self._institutions_by_id[institution_id]

    def actors_for_network(self, network: str) -> list[Actor]:
        """Return list of actors eligible to transact on the given network."""
        return self._actors_by_network.get(network.lower(), [])

    def merchants_for_country(self, country: str) -> list[Actor]:
        """Return list of merchants in a given country."""
        return self._merchants_by_country.get(country.upper(), [])


def build_world(config: GeneratorConfig, calibration: CalibrationProfile) -> World:
    """Build the synthetic world of institutions and actors with substream independence."""
    factory = RngFactory(config.seed)

    # 1. Build institutions
    inst_rng = factory.stream("world", "institutions")
    num_institutions = config.institutions
    institutions: list[Institution] = []

    inst_countries = ["IN", "US", "GB", "DE", "AE", "SG", "FR", "NL"]
    for idx in range(num_institutions):
        inst_id = f"I{idx + 1:02d}"
        c_code = inst_countries[idx % len(inst_countries)]
        bic_code = f"ZRP{chr(65 + (idx % 26))}"
        inst = Institution(
            institution_id=inst_id,
            name=f"Synthetic Institution {inst_id}",
            country=c_code,
            bic=bic(bank_code=bic_code, country=c_code, branch="XXX"),
            upi_handle=f"rplbank{idx + 1}",
            ifsc_prefix=f"RPL{chr(65 + (idx % 26))}",
            issuer_id=f"ISS{idx + 1:02d}",
            acquirer_id=f"ACQ{idx + 1:02d}",
            size_weight=float(1.0 + inst_rng.uniform(0.0, 1.5)),
        )
        institutions.append(inst)

    # 2. Demographics ratios
    ind_ratio = float(calibration.get("world.individual_ratio", 0.85))
    merch_ratio = float(calibration.get("world.merchant_ratio", 0.10))
    biz_ratio = float(calibration.get("world.business_ratio", 0.03))

    num_exchanges = int(calibration.get("world.exchange_count", 4))
    num_bridges = int(calibration.get("world.bridge_count", 2))
    num_mixers = int(calibration.get("world.mixer_count", 2))
    num_defi = int(calibration.get("world.defi_count", 3))
    num_gambling = int(calibration.get("world.gambling_count", 2))
    num_psp = int(calibration.get("world.psp_count", 3))

    country_weights_dict: dict[str, float] = calibration.get("world.country_weights")
    countries = list(country_weights_dict.keys())
    c_weights = [country_weights_dict[c] for c in countries]

    link_rate = float(calibration.get("world.cross_network_link_rate", 0.40))
    pareto_alpha = float(calibration.get("world.activity_pareto_alpha", 1.8))
    mule_fraction = float(calibration.get("world.mule_pool_fraction", 0.05))
    burner_fraction = float(calibration.get("world.burner_fraction", 0.30))

    sim_start = config.start_time
    total_actors = config.actors
    actors: list[Actor] = []

    # Assign actor kinds deterministically
    # Fixed services assigned first
    service_assignments: list[str] = (
        ["exchange"] * num_exchanges
        + ["bridge"] * num_bridges
        + ["mixer"] * num_mixers
        + ["defi"] * num_defi
        + ["gambling"] * num_gambling
        + ["psp"] * num_psp
    )

    for i in range(total_actors):
        actor_id = f"A{i + 1:06d}"
        core_rng = factory.stream("world", "actor", str(i), "core")

        # Determine actor kind
        if i < len(service_assignments):
            kind = service_assignments[i]
        else:
            r = core_rng.random()
            if r < ind_ratio:
                kind = "individual"
            elif r < ind_ratio + merch_ratio:
                kind = "merchant"
            elif r < ind_ratio + merch_ratio + biz_ratio:
                kind = "business"
            else:
                kind = "individual"

        # Country & Timezone
        if kind in {"exchange", "bridge", "mixer", "defi", "gambling"}:
            c_code = "US" if kind == "exchange" else core_rng.choice(["US", "GB", "SG", "DE"])
        else:
            c_code = core_rng.weighted_choice(countries, c_weights)

        tz_name = COUNTRY_SPECS[c_code]["timezone"]
        primary_inst = core_rng.choice(institutions)

        # Account creation timestamp
        # Most created before simulation (aged), burner accounts created during simulation
        is_burner = core_rng.bernoulli(burner_fraction)
        if is_burner:
            created_offset_hours = core_rng.uniform(0.0, float(config.days * 24))
            created_at = sim_start + timedelta(hours=created_offset_hours)
        else:
            aged_days = core_rng.uniform(30.0, 730.0)
            created_at = sim_start - timedelta(days=aged_days)

        # Activity multiplier (heavy-tailed Pareto)
        activity_multiplier = core_rng.pareto(pareto_alpha, 0.5)

        # Merchant MCC
        actor_mcc = core_rng.choice(MERCHANT_MCCS) if kind == "merchant" else None

        # Shared contact points
        shared_phone: str | None = None
        shared_email: str | None = None
        if core_rng.bernoulli(link_rate):
            shared_phone = phone(core_rng)
            shared_email = email(core_rng, username=f"actor{i + 1:06d}")

        # Substream isolation for each rail: ALWAYS sample all 5 rails
        # Rail 1: UPI
        upi_rng = factory.stream("world", "actor", str(i), "upi")
        upi_identities: dict[str, Any] = {}
        # UPI is Indian domestic; available if country is IN or cross-border remittance corridor
        if c_code == "IN" or kind in {"exchange", "psp"}:
            u_vpa = vpa(upi_rng, username=f"user{i + 1:06d}", handle=primary_inst.upi_handle)
            u_ifsc = ifsc(bank_code=primary_inst.ifsc_prefix, branch_index=1)
            u_acc = bank_account_number(upi_rng, length=11)
            u_ph = shared_phone or phone(upi_rng)
            upi_identities = {
                "vpa": u_vpa,
                "ifsc": u_ifsc,
                "account_number": u_acc,
                "phone": u_ph,
            }

        # Rail 2: Card
        card_rng = factory.stream("world", "actor", str(i), "card")
        card_identities: dict[str, Any] = {}
        if kind in {"individual", "merchant", "business"}:
            num_cards = card_rng.randint(1, 3) if kind == "individual" else 1
            tokens = [card_token(card_rng) for _ in range(num_cards)]
            bins = [card_bin(card_rng) for _ in range(num_cards)]
            last4s = [card_last4(card_rng) for _ in range(num_cards)]
            card_identities = {
                "tokens": tokens,
                "bins": bins,
                "last4s": last4s,
                "issuer_id": primary_inst.issuer_id,
                "acquirer_id": primary_inst.acquirer_id,
            }
            if kind == "merchant":
                card_identities["merchant_id"] = merchant_id(card_rng)
                card_identities["terminal_id"] = terminal_id(card_rng)

        # Rail 3: Crypto
        crypto_rng = factory.stream("world", "actor", str(i), "crypto")
        crypto_identities: dict[str, Any] = {}
        # Crypto available to exchanges, services, and a fraction of individuals/businesses
        is_crypto_user = kind in {
            "exchange",
            "bridge",
            "mixer",
            "defi",
            "gambling",
        } or crypto_rng.bernoulli(0.35)
        if is_crypto_user:
            addresses: dict[str, list[str]] = {
                "evm": [crypto_address("evm", crypto_rng)],
                "bitcoin": [crypto_address("bitcoin", crypto_rng)],
                "solana": [crypto_address("solana", crypto_rng)],
                "tron": [crypto_address("tron", crypto_rng)],
            }
            crypto_identities = {"addresses": addresses}
            if kind in {"exchange", "bridge", "mixer", "defi", "gambling"}:
                crypto_identities["service_category"] = kind

        # Rail 4: SWIFT
        swift_rng = factory.stream("world", "actor", str(i), "swift")
        swift_identities: dict[str, Any] = {}
        # SWIFT available to businesses, institutions, exchanges, and high-value individuals
        is_swift_user = kind in {"business", "exchange", "bank"} or swift_rng.bernoulli(0.20)
        if is_swift_user:
            spec = COUNTRY_SPECS[c_code]
            if spec["bban_format"] == "iban":
                s_iban = iban(
                    country=c_code,
                    bank_code=primary_inst.bic[:4],
                    account_num=f"{i + 1:08d}",
                    rng=swift_rng,
                )
                swift_identities = {
                    "iban": s_iban,
                    "bic": primary_inst.bic,
                    "account_type": "iban",
                }
            else:
                s_acc = bank_account_number(swift_rng, length=12)
                swift_identities = {
                    "account_number": s_acc,
                    "bic": primary_inst.bic,
                    "account_type": "national",
                }

        # Rail 5: Wallet
        wallet_rng = factory.stream("world", "actor", str(i), "wallet")
        wallet_identities: dict[str, Any] = {}
        # Wallet available to individuals, merchants, and PSPs
        if kind in {"individual", "merchant", "psp", "business"}:
            w_acc = f"w_{i + 1:06d}"
            w_em = shared_email or email(wallet_rng, username=f"wallet{i + 1:06d}")
            w_ph = shared_phone or phone(wallet_rng)
            num_instr = wallet_rng.randint(1, 3)
            instruments = [instrument_id(wallet_rng) for _ in range(num_instr)]
            wallet_identities = {
                "account_id": w_acc,
                "email": w_em,
                "phone": w_ph,
                "instruments": instruments,
            }

        # Ensure every actor has at least one valid identity across the ecosystem
        if not (
            upi_identities
            or card_identities
            or crypto_identities
            or swift_identities
            or wallet_identities
        ):
            # Fallback wallet identity
            w_acc = f"w_{i + 1:06d}"
            wallet_identities = {
                "account_id": w_acc,
                "email": email(wallet_rng, username=f"wallet{i + 1:06d}"),
                "phone": phone(wallet_rng),
                "instruments": [instrument_id(wallet_rng)],
            }

        actor = Actor(
            actor_id=actor_id,
            actor_index=i,
            name=placeholder_name(i + 1),
            kind=kind,
            country=c_code,
            timezone=tz_name,
            institution_id=primary_inst.institution_id,
            created_at=created_at,
            activity_multiplier=activity_multiplier,
            mcc=actor_mcc,
            shared_phone=shared_phone,
            shared_email=shared_email,
            upi_identities=upi_identities,
            card_identities=card_identities,
            crypto_identities=crypto_identities,
            swift_identities=swift_identities,
            wallet_identities=wallet_identities,
        )
        actors.append(actor)

    # 3. Select mule pool from individuals
    mule_rng = factory.stream("world", "mule_pool")
    individuals = [a for a in actors if a.kind == "individual"]
    mule_count = max(2, int(len(individuals) * mule_fraction))
    mule_pool = mule_rng.sample_without_replacement(individuals, mule_count)

    return World(
        config=config,
        institutions=institutions,
        actors=actors,
        mule_pool=mule_pool,
    )
