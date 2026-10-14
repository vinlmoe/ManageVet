"""Paramètres économiques de la simulation.

Toutes les valeurs ont un défaut raisonnable ; chaque serveur de jeu peut en
surcharger une partie via ``GameServer.parameters`` (JSON). C'est le levier
principal des « stress tests » : choc de demande, hausse des prix
fournisseurs, charges sociales, etc.
"""

from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True)
class SimulationParameters:
    # --- Carte ---------------------------------------------------------------
    cell_size_km: float = 2.0

    # --- Choix de la clinique par la patientèle (modèle logit) --------------
    base_utility: float = 2.0  # attrait d'une clinique « moyenne » à 0 km
    outside_option_utility: float = 0.0  # ne pas consulter ce trimestre
    beta_distance: float = 0.30  # pénalité par km
    beta_price: float = 3.0  # pénalité par point d'indice prix (1.0 = prix de référence)
    beta_offer: float = 1.0  # bonus pour une offre de services complète
    beta_reputation: float = 1.0  # bonus de réputation (centrée sur 0.5)

    # --- Volume de demande ---------------------------------------------------
    demand_multiplier: float = 1.0  # choc global de demande (crise, épidémie…)
    service_price_elasticity: float = 0.5  # élasticité des actes à leur propre prix
    product_price_elasticity: float = 1.5  # élasticité des ventes d'aliments au prix
    noise: float = 0.05  # aléa multiplicatif (+/-) sur les volumes

    # --- Capacité de production ---------------------------------------------
    vet_hours_per_quarter: float = 220.0  # heures facturables (~55 % du temps de travail)
    asv_hours_per_quarter: float = 420.0  # heures productives par ASV

    # --- Coûts ------------------------------------------------------------------
    employer_contribution_rate: float = 0.42  # charges patronales / salaire brut
    severance_months: float = 1.0  # indemnité de rupture (mois de brut)
    purchase_cost_multiplier: float = 1.0  # choc sur les prix fournisseurs
    other_fixed_costs_per_quarter: float = 6000.0  # énergie, assurances, télécom…
    overdraft_rate_per_quarter: float = 0.02  # agios sur découvert

    # --- Réputation -----------------------------------------------------------
    reputation_inertia: float = 0.7

    @classmethod
    def from_overrides(cls, overrides: dict | None) -> "SimulationParameters":
        """Construit les paramètres en ignorant les clés inconnues."""
        known = {f.name for f in fields(cls)}
        clean = {k: float(v) for k, v in (overrides or {}).items() if k in known}
        return cls(**clean)

    def as_dict(self) -> dict:
        return asdict(self)
