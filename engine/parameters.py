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

    # --- Convention collective (voir clinics/ccn.py) ---------------------------
    ccn_point_value: float = 17.96  # valeur du point 2026 (avenant n° 91)
    ccn_vet_night_majoration: float = 0.20  # nuit, dimanche, jour férié (vétérinaires)
    ccn_asv_night_majoration: float = 0.15  # nuit, dimanche, jour férié (personnel non vétérinaire)
    ccn_hours_per_fte_quarter: float = 401.75  # 1 607 h annuelles / 4

    # --- Formats d'établissement ---------------------------------------------
    # Low-cost : actes standardisés (protocoles, peu de temps « hors acte »),
    # achats groupés de consommables, mais offre restreinte aux actes de base.
    lowcost_time_factor: float = 0.75
    lowcost_consumables_factor: float = 0.85
    # CHV (arrêté du 13 mars 2015 et cahier des charges de l'Ordre, à vérifier) :
    chv_min_vets: float = 6  # vétérinaires à temps plein
    chv_min_asv: float = 6  # personnes qualifiées à temps plein
    chv_min_specialists: float = 2  # vétérinaires spécialistes (R. 242-34 CRPM)
    chv_min_specialties: float = 2  # disciplines distinctes parmi médecine interne, chirurgie, imagerie
    chv_min_imaging: float = 3  # techniques d'imagerie parmi radio, écho, scanner, IRM
    chv_utility_bonus: float = 0.6  # attractivité (urgences, référés, réputation)
    # Permanence 24 h/24 : un vétérinaire et une personne qualifiée sur place.
    # 96 h/semaine hors ouverture de jour (8 h-20 h du lundi au samedi) x 13 semaines.
    chv_permanence_hours_per_quarter: float = 1248.0
    # Heures majorées (22 h-7 h, dimanches et jours fériés) par trimestre et par poste.
    chv_majored_hours_per_quarter: float = 1060.0
    chv_night_productivity: float = 0.3  # activité facturable la nuit / activité de jour
    chv_extra_fixed_costs_per_quarter: float = 8000.0  # énergie 24 h/24, maintenance, sécurité

    # --- Urgences et cas référés (bassin régional) -------------------------------
    # Pour ces actes, les propriétaires (ou leur vétérinaire traitant) acceptent
    # d'aller plus loin : sensibilité à la distance plus faible.
    referral_base_utility: float = 1.0
    referral_beta_distance: float = 0.08  # par km

    # --- Emprunts bancaires -----------------------------------------------------
    loan_base_rate_annual: float = 0.045
    loan_risk_premium_per_leverage: float = 0.01  # +1 point par unité de dette / capitaux propres
    loan_loss_premium: float = 0.01  # si le dernier trimestre est déficitaire
    loan_max_leverage: float = 3.0  # dette totale <= 3 x capitaux propres
    loan_max_amount: float = 400000.0
    loan_early_repayment_penalty: float = 0.03  # indemnité de remboursement anticipé

    @classmethod
    def from_overrides(cls, overrides: dict | None) -> "SimulationParameters":
        """Construit les paramètres en ignorant les clés inconnues."""
        known = {f.name for f in fields(cls)}
        clean = {k: float(v) for k, v in (overrides or {}).items() if k in known}
        return cls(**clean)

    def as_dict(self) -> dict:
        return asdict(self)
