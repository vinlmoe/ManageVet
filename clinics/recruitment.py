"""Difficulté de recrutement : chances de succès, vivier de candidats, frais.

Chaque décision de recrutement est une tentative, tranchée en début de
résolution pour toutes les cliniques du serveur à la fois :

1. Chances individuelles :

       p = base(profil) x pénurie x facteur salaire x facteur lieu
           x facteur réputation x facteur format          (borné à [2 %, 98 %])

   - facteur salaire = (salaire proposé / salaire du marché) ^ élasticité,
     plafonné ; salaire du marché = minimum conventionnel x (1 + prime) ;
   - facteur lieu = 1 - pénalité rurale x (1 - densité), la densité étant la
     patientèle du rayon de chalandise rapportée à la case la plus dense ;
   - facteur réputation = 1 + poids x (2 x réputation - 1) ;
   - CHV : bonus pour attirer les spécialistes (plateau technique), malus
     pour les généralistes (gardes de nuit).

2. Vivier : chaque trimestre, le marché compte un nombre limité de candidats
   par profil (généralistes, ASV, et pour les spécialistes : par
   discipline). Si les candidats acceptant sont plus nombreux que le
   vivier, les offres les plus attractives l'emportent ; les autres
   échouent (« le candidat a rejoint une clinique concurrente »).

3. Les frais d'annonce ou de cabinet sont dus pour chaque tentative,
   qu'elle aboutisse ou non (compte 6231).

Toutes les valeurs sont des hypothèses de jeu, paramétrables par serveur.
"""

import random
from decimal import Decimal

from accounting import pcg
from accounting.services import post_entry

from . import ccn
from .models import Specialty, StaffMember

VET, SPECIALIST, ASV = "vet", "specialist", "asv"
PROFILE_LABELS = {VET: "vétérinaire généraliste", SPECIALIST: "vétérinaire spécialiste", ASV: "auxiliaire"}


def profile(role: str, specialty: str = "") -> str:
    if role == StaffMember.Role.ASV:
        return ASV
    return SPECIALIST if specialty else VET


def market_salary(role: str, level: int, specialty: str, params) -> Decimal:
    premium = getattr(params, f"market_premium_{profile(role, specialty)}")
    minimum = ccn.minimum_salary(role, level, params.ccn_point_value)
    return (minimum * Decimal(str(1 + premium))).quantize(Decimal("1"))


def density_index(clinic, insights=None) -> float:
    from game.placement import cell_insights

    insights = insights or cell_insights(clinic.server)
    best = max((i.catchment for i in insights.values()), default=0) or 1
    return insights[(clinic.x, clinic.y)].catchment / best


def clinic_factors(clinic, params, insights=None) -> dict:
    """Facteurs propres à la clinique, communs à toutes ses offres."""
    density = density_index(clinic, insights)
    location = 1 - params.recruitment_rural_penalty * (1 - density)
    reputation = 1 + params.recruitment_reputation_weight * (2 * clinic.reputation - 1)
    chv = clinic.format == clinic.Format.CHV
    return {
        "density": density,
        "location": location,
        "reputation": reputation,
        "format": {
            VET: 1 - params.recruitment_chv_night_penalty if chv else 1.0,
            SPECIALIST: 1 + params.recruitment_chv_specialist_bonus if chv else 1.0,
            ASV: 1.0,
        },
    }


def chance(clinic, role: str, level: int, specialty: str, salary, params, factors=None) -> float:
    """Probabilité qu'un candidat accepte l'offre (avant concurrence entre cliniques)."""
    if not params.recruitment_enabled:
        return 1.0
    factors = factors or clinic_factors(clinic, params)
    kind = profile(role, specialty)
    ratio = float(Decimal(str(salary)) / market_salary(role, level, specialty, params))
    salary_factor = min(ratio**params.recruitment_salary_elasticity, params.recruitment_salary_factor_cap)
    p = (
        getattr(params, f"recruitment_base_{kind}")
        * params.recruitment_shortage
        * salary_factor
        * factors["location"]
        * factors["reputation"]
        * factors["format"][kind]
    )
    return max(0.02, min(0.98, p))


def fee(role: str, specialty: str, params) -> Decimal:
    return Decimal(str(getattr(params, f"recruitment_fee_{profile(role, specialty)}")))


def pool_key(role: str, specialty: str) -> str:
    kind = profile(role, specialty)
    return f"{kind}:{specialty}" if kind == SPECIALIST else kind


def pool_size(key: str, params) -> int:
    kind = key.split(":")[0]
    return int(getattr(params, f"candidates_{kind}_per_quarter"))


def resolve_market(server, quarter: int, params) -> list[dict]:
    """Tranche toutes les tentatives de recrutement du trimestre sur le serveur.

    Les tentatives réussies restent « en attente » (le recrutement est
    exécuté ensuite avec les autres décisions) ; les échecs sont rejetés avec
    leur motif. Renvoie la liste des issues, pour le rapport.
    """
    from clinics.services import OperationError, check_offer
    from decisions.models import PendingDecision
    from game.placement import cell_insights

    if not params.recruitment_enabled:
        return []
    decisions = list(
        PendingDecision.objects.filter(
            clinic__server=server, quarter=quarter, kind=PendingDecision.Kind.HIRE, status=PendingDecision.Status.PENDING
        )
        .select_related("clinic__server")
        .order_by("id")
    )
    if not decisions:
        return []
    rng = random.Random(f"{server.seed}-{quarter}-recrutement")
    insights = cell_insights(server)
    factors = {}
    accepted: dict[str, list] = {}
    outcomes = []

    def reject(decision, message):
        decision.status = PendingDecision.Status.REJECTED
        decision.message = message[:255]
        decision.save(update_fields=["status", "message"])

    for decision in decisions:
        clinic, p = decision.clinic, decision.payload
        role, specialty = p.get("role"), p.get("specialty", "")
        try:
            level = check_offer(clinic, role, p.get("level"), specialty, Decimal(str(p.get("gross_monthly_salary"))))
        except (OperationError, ArithmeticError, TypeError, ValueError):
            continue  # offre invalide : rejetée ensuite par le gestionnaire de décisions, sans frais
        if clinic.pk not in factors:
            factors[clinic.pk] = clinic_factors(clinic, params, insights)
        probability = chance(clinic, role, level, specialty, p["gross_monthly_salary"], params, factors[clinic.pk])
        cost = fee(role, specialty, params)
        post_entry(
            clinic, quarter, f"Frais de recrutement – {p.get('name', 'candidat')}", [(pcg.RECRUITMENT, cost, 0), (pcg.BANK, 0, cost)]
        )
        outcome = {"clinic": clinic.pk, "decision": decision.pk, "profile": profile(role, specialty), "chance": round(probability, 3)}
        if rng.random() >= probability:
            reject(decision, f"Recrutement infructueux : aucun candidat n'a accepté l'offre (chances estimées {probability:.0%}).")
            outcome["result"] = "no_candidate"
        else:
            # L'attractivité de l'offre départage les cliniques qui visent le même vivier.
            accepted.setdefault(pool_key(role, specialty), []).append((probability * rng.uniform(0.8, 1.2), decision, outcome))
        outcomes.append(outcome)

    for key, offers in accepted.items():
        offers.sort(key=lambda item: item[0], reverse=True)
        size = pool_size(key, params)
        for rank, (_, decision, outcome) in enumerate(offers):
            if rank < size:
                outcome["result"] = "hired"
            else:
                label = PROFILE_LABELS[key.split(":")[0]]
                if ":" in key:
                    label += f" en {Specialty(key.split(':')[1]).label.lower()}"
                reject(
                    decision,
                    f"Recrutement infructueux : les {size} candidat(s) {label} disponibles ce trimestre "
                    "ont choisi des offres plus attractives.",
                )
                outcome["result"] = "lost_to_competitor"
    return outcomes
