"""Arbitrage de la patientèle entre cliniques.

Module volontairement indépendant de Django : il ne manipule que des
structures simples, ce qui le rend facile à tester et à réutiliser pour des
simulations hors ligne.

Modèle : logit multinomial avec option extérieure. Pour une zone ``z`` et une
clinique ``c`` :

    U(z, c) = base
              - beta_distance   * distance_km(z, c)
              - beta_price      * (indice_prix(c) - 1) * sensibilité_prix(z)
              + beta_offer      * (ratio_offre(c) - 1)
              + beta_reputation * (réputation(c) - 0.5)
              + bonus_format(c)                       (CHV)

    part(z, c) = exp(U(z, c)) / (exp(U0) + somme_c' exp(U(z, c')))

La part restante correspond aux animaux non médicalisés ce trimestre.
"""

import math
from dataclasses import dataclass, replace

from .parameters import SimulationParameters

SPECIES = ("dogs", "cats")


@dataclass(frozen=True)
class Zone:
    id: int
    x: int
    y: int
    dogs: float
    cats: float
    price_sensitivity: float = 1.0


@dataclass(frozen=True)
class ClinicOffer:
    id: int
    x: int
    y: int
    price_index: float  # 1.0 = prix de référence du marché
    offer_ratio: float  # 0..1, part pondérée des services proposés
    reputation: float  # 0..1
    bonus: float = 0.0  # attractivité propre au format (CHV)


def distance_km(ax: int, ay: int, bx: int, by: int, cell_size_km: float) -> float:
    return math.hypot(ax - bx, ay - by) * cell_size_km


def utility(zone: Zone, clinic: ClinicOffer, p: SimulationParameters) -> float:
    d = distance_km(zone.x, zone.y, clinic.x, clinic.y, p.cell_size_km)
    return (
        p.base_utility
        - p.beta_distance * d
        - p.beta_price * (clinic.price_index - 1.0) * zone.price_sensitivity
        + p.beta_offer * (clinic.offer_ratio - 1.0)
        + p.beta_reputation * (clinic.reputation - 0.5)
        + clinic.bonus
    )


def zone_shares(zone: Zone, clinics: list[ClinicOffer], p: SimulationParameters) -> dict[int, float]:
    """Parts de marché de chaque clinique dans une zone (somme <= 1)."""
    if not clinics:
        return {}
    utilities = {c.id: utility(zone, c, p) for c in clinics}
    # Stabilité numérique : on soustrait le maximum avant exp().
    m = max(max(utilities.values()), p.outside_option_utility)
    exp_outside = math.exp(p.outside_option_utility - m)
    exps = {cid: math.exp(u - m) for cid, u in utilities.items()}
    total = exp_outside + sum(exps.values())
    return {cid: e / total for cid, e in exps.items()}


def allocate_patients(
    zones: list[Zone], clinics: list[ClinicOffer], p: SimulationParameters
) -> dict[int, dict[str, float]]:
    """Nombre d'animaux (chiens/chats) attirés par chaque clinique ce trimestre."""
    result = {c.id: {s: 0.0 for s in SPECIES} for c in clinics}
    for zone in zones:
        for cid, share in zone_shares(zone, clinics, p).items():
            result[cid]["dogs"] += zone.dogs * share * p.demand_multiplier
            result[cid]["cats"] += zone.cats * share * p.demand_multiplier
    return result


def allocate_referrals(
    zones: list[Zone], clinics: list[ClinicOffer], p: SimulationParameters
) -> dict[int, float]:
    """Bassin régional d'un acte (urgences, cas référés) entre les cliniques qui le proposent.

    Même logit, mais seules comptent la distance (avec une sensibilité réduite)
    et la réputation : le prix et l'offre généraliste n'interviennent pas.
    """
    referral = replace(
        p,
        base_utility=p.referral_base_utility,
        beta_distance=p.referral_beta_distance,
        beta_price=0.0,
        beta_offer=0.0,
    )
    neutral = [replace(c, price_index=1.0, offer_ratio=1.0, bonus=0.0) for c in clinics]
    allocation = allocate_patients(zones, neutral, referral)
    return {cid: a["dogs"] + a["cats"] for cid, a in allocation.items()}


def price_volume_factor(price: float, reference_price: float, elasticity: float) -> float:
    """Facteur de volume à élasticité constante : (prix / référence) ^ -élasticité."""
    if price <= 0 or reference_price <= 0:
        return 1.0
    return (price / reference_price) ** (-elasticity)
