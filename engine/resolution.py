"""Résolution d'un trimestre de jeu (tâche hebdomadaire).

Ordre des étapes pour un serveur :

0. Marché du travail : les tentatives de recrutement de toutes les cliniques
   sont tranchées ensemble (chances, vivier limité, frais d'annonce).
1. Application des décisions en attente de chaque clinique (le changement
   de format en dernier), puis contrôle des conditions du statut CHV.
2. Réassort automatique des stocks jusqu'au niveau cible (achats 607).
3. Arbitrage de la patientèle entre toutes les cliniques (modèle logit).
4. Par clinique : actes réalisés dans la limite de la capacité du personnel
   (706 + consommables 6022), ventes de produits dans la limite du stock
   (707), charges fixes (salaires, loyer, honoraires, leasing, divers,
   surcoûts CHV), échéances d'emprunts (6611 + 164), dotations aux
   amortissements, agios, variation de stock (6037).
5. Mise à jour de la réputation, rapport trimestriel figé.
6. Passage au trimestre suivant (ou fin de partie).

Tout est exécuté dans une transaction : un trimestre est résolu entièrement
ou pas du tout. Le hasard est tiré d'un générateur initialisé par
(graine du serveur, trimestre, position de la clinique) : rejouer une partie avec les mêmes
décisions donne exactement les mêmes résultats.
"""

import random
from dataclasses import dataclass, field
from datetime import timedelta
from decimal import Decimal

from django.db import transaction
from django.utils import timezone

from accounting import pcg
from accounting.loans import pay_installments
from accounting.models import QuarterReport
from accounting.services import equity, income_statement, money, outstanding_debt, post_entry
from clinics import ccn
from clinics.formats import clinic_capabilities, enforce_chv_status, unavailability_reason
from clinics.recruitment import resolve_market
from clinics.models import Clinic, StaffMember
from clinics.services import active_equipment, active_staff
from decisions.handlers import apply_pending_decisions
from game.models import GameServer
from inventory.models import ProductLine

from . import demand
from .parameters import SimulationParameters

ZERO = Decimal("0")


@dataclass
class ClinicOutcome:
    """Indicateurs d'activité calculés pour une clinique pendant la résolution."""

    patients: dict = field(default_factory=lambda: {"dogs": 0.0, "cats": 0.0})
    acts: float = 0.0
    capacity_ratio: float = 1.0
    general_ratio: float = 1.0
    stock_fill_rate: float = 1.0
    stock_value_start: Decimal = ZERO
    details: dict = field(default_factory=dict)


# --------------------------------------------------------------------------
# Point d'entrée
# --------------------------------------------------------------------------


@transaction.atomic
def resolve_quarter(server: GameServer) -> list[QuarterReport]:
    server = GameServer.objects.select_for_update().get(pk=server.pk)
    if server.status != GameServer.Status.RUNNING:
        raise ValueError(f"Le serveur « {server} » n'est pas en cours de partie.")

    params = server.get_parameters()
    quarter = server.current_quarter
    clinics = list(server.clinics.select_related("server").order_by("id"))
    outcomes = {c.id: ClinicOutcome() for c in clinics}

    # Marché du travail : toutes les tentatives de recrutement du serveur sont
    # tranchées ensemble (vivier limité, concurrence entre cliniques).
    hiring = resolve_market(server, quarter, params)
    for clinic in clinics:
        outcomes[clinic.id].details["recruitment"] = [o for o in hiring if o["clinic"] == clinic.id]

    for clinic in clinics:
        apply_pending_decisions(clinic, quarter, params)
        clinic.refresh_from_db()
        event = enforce_chv_status(clinic, quarter, params)
        outcomes[clinic.id].details["events"] = [event] if event else []
        outcomes[clinic.id].stock_value_start = _stock_value(clinic)
        _restock(clinic, quarter, params)

    patients = _allocate_market(server, clinics, params)
    total_patients = sum(p["dogs"] + p["cats"] for p in patients.values()) or 1.0
    referrals = _allocate_referrals(server, clinics, params)

    reports = []
    for clinic in clinics:
        out = outcomes[clinic.id]
        out.patients = patients[clinic.id]
        rng = random.Random(f"{server.seed}-{quarter}-{clinic.x}-{clinic.y}")
        _produce_services(clinic, quarter, out, params, rng, referrals)
        _sell_products(clinic, quarter, out, params, rng)
        _post_fixed_charges(clinic, quarter, params)
        pay_installments(clinic, quarter)
        _post_depreciation(clinic, quarter)
        _post_stock_variation(clinic, quarter, out)
        _post_overdraft_interest(clinic, quarter, params)
        _update_reputation(clinic, out, params, quarter)
        market_share = (out.patients["dogs"] + out.patients["cats"]) / total_patients
        reports.append(_build_report(clinic, quarter, out, market_share))

    _advance(server)
    return reports


def resolve_due_servers(now=None) -> list[GameServer]:
    """Résout tous les serveurs dont l'échéance hebdomadaire est passée."""
    now = now or timezone.now()
    due = GameServer.objects.filter(status=GameServer.Status.RUNNING, next_resolution_at__lte=now)
    resolved = []
    for server in due:
        resolve_quarter(server)
        resolved.append(server)
    return resolved


# --------------------------------------------------------------------------
# Étapes
# --------------------------------------------------------------------------


def _stock_value(clinic: Clinic) -> Decimal:
    return sum((item.value for item in clinic.stock.all()), ZERO)


def _restock(clinic: Clinic, quarter: int, params: SimulationParameters):
    """Recomplète chaque gamme jusqu'à son niveau cible (une commande groupée)."""
    total = ZERO
    for item in clinic.stock.select_related("product_line"):
        missing = item.target_quantity - item.quantity
        if missing <= 0:
            continue
        unit_cost = money(item.product_line.unit_cost * Decimal(str(params.purchase_cost_multiplier)))
        # Coût moyen unitaire pondéré après entrée en stock.
        old_value = item.average_unit_cost * item.quantity
        item.quantity += missing
        item.average_unit_cost = money((old_value + unit_cost * missing) / item.quantity)
        item.save(update_fields=["quantity", "average_unit_cost"])
        total += unit_cost * missing
    post_entry(clinic, quarter, "Réassort marchandises", [(pcg.PURCHASES_GOODS, total, 0), (pcg.BANK, 0, total)])


def _price_index_and_offer(clinic: Clinic, quarter: int) -> tuple[float, float]:
    """Indice de prix (pondéré par le panier de référence) et ratio d'offre.

    Calculés sur l'offre généraliste : les actes réservés aux CHV ou aux
    spécialistes n'entrent pas dans le panier (leur effet passe par le bonus
    d'attractivité du CHV et par le chiffre d'affaires qu'ils génèrent).
    """
    caps = clinic_capabilities(clinic, quarter)
    weighted_price = weight_offered = weight_total = 0.0
    for cs in clinic.services.select_related("service_type__required_equipment").filter(
        service_type__required_format="", service_type__required_specialty=""
    ):
        st = cs.service_type
        w = st.market_weight
        weight_total += w
        if _is_operable(clinic, cs, caps):
            weight_offered += w
            weighted_price += w * float(cs.price) / float(st.reference_price)
    price_index = weighted_price / weight_offered if weight_offered else 1.0
    offer_ratio = weight_offered / weight_total if weight_total else 0.0
    return price_index, offer_ratio


def _is_operable(clinic, cs, caps) -> bool:
    return cs.active and unavailability_reason(clinic.format, cs.service_type, caps) is None


def _allocate_market(server: GameServer, clinics: list[Clinic], params: SimulationParameters):
    offers = []
    for clinic in clinics:
        if not active_staff(clinic, server.current_quarter).filter(role=StaffMember.Role.VET).exists():
            continue  # Sans vétérinaire, la clinique ne peut pas recevoir de patients.
        price_index, offer_ratio = _price_index_and_offer(clinic, server.current_quarter)
        bonus = params.chv_utility_bonus if clinic.format == Clinic.Format.CHV else 0.0
        offers.append(
            demand.ClinicOffer(clinic.id, clinic.x, clinic.y, price_index, offer_ratio, clinic.reputation, bonus)
        )
    zones = [
        demand.Zone(z.id, z.x, z.y, z.dogs, z.cats, z.price_sensitivity)
        for z in server.zones.all()
    ]
    allocation = demand.allocate_patients(zones, offers, params)
    return {c.id: allocation.get(c.id, {"dogs": 0.0, "cats": 0.0}) for c in clinics}


def _allocate_referrals(server: GameServer, clinics: list[Clinic], params: SimulationParameters):
    """Bassins régionaux des actes à recrutement régional (urgences, cas référés).

    Renvoie {code de prestation: {id de clinique: animaux du bassin}}.
    """
    from clinics.models import ServiceType

    quarter = server.current_quarter
    zones = [demand.Zone(z.id, z.x, z.y, z.dogs, z.cats, z.price_sensitivity) for z in server.zones.all()]
    caps = {c.id: clinic_capabilities(c, quarter) for c in clinics}
    staffed = {c.id for c in clinics if active_staff(c, quarter).filter(role=StaffMember.Role.VET).exists()}
    pools = {}
    for st in ServiceType.objects.filter(referral=True).select_related("required_equipment"):
        offering = [
            demand.ClinicOffer(c.id, c.x, c.y, 1.0, 1.0, c.reputation)
            for c in clinics
            if c.id in staffed
            and c.services.filter(service_type=st, active=True).exists()
            and unavailability_reason(c.format, st, caps[c.id]) is None
        ]
        pools[st.code] = demand.allocate_referrals(zones, offering, params) if offering else {}
    return pools


def _noise(rng: random.Random, params: SimulationParameters) -> float:
    return 1.0 + rng.uniform(-params.noise, params.noise)


GENERAL_POOL = "general"


def vet_pools(clinic, quarter, params) -> dict[str, float]:
    """Heures vétérinaires facturables par réserve de temps.

    - « general » : vétérinaires généralistes, qui réalisent tous les actes
      sans spécialité (consultations, vaccinations, urgences, hospitalisation…) ;
    - une réserve par spécialité : l'activité d'un spécialiste est restreinte
      aux actes de sa discipline (cas référés).

    Chaque vétérinaire apporte ``vet_hours_per_quarter`` x la productivité de
    son échelon. Pour un CHV, la permanence 24 h/24 est prise d'abord sur les
    généralistes, puis, à défaut, sur les spécialistes.
    """
    pools: dict[str, float] = {GENERAL_POOL: 0.0}
    for member in active_staff(clinic, quarter).filter(role=StaffMember.Role.VET):
        key = member.specialty or GENERAL_POOL
        pools[key] = pools.get(key, 0.0) + ccn.productivity("vet", member.level, params) * params.vet_hours_per_quarter
    if clinic.format == Clinic.Format.CHV:
        lost = permanence_fte(params) * (1 - params.chv_night_productivity) * params.vet_hours_per_quarter
        taken = min(lost, pools[GENERAL_POOL])
        pools[GENERAL_POOL] -= taken
        remaining = lost - taken
        specialist_total = sum(h for k, h in pools.items() if k != GENERAL_POOL)
        if remaining and specialist_total:
            for key in pools:
                if key != GENERAL_POOL:
                    pools[key] = max(0.0, pools[key] - remaining * pools[key] / specialist_total)
    return pools


def asv_hours(clinic, quarter, params) -> float:
    hours = sum(
        ccn.productivity("asv", m.level, params) * params.asv_hours_per_quarter
        for m in active_staff(clinic, quarter).filter(role=StaffMember.Role.ASV)
    )
    if clinic.format == Clinic.Format.CHV:
        hours -= permanence_fte(params) * (1 - params.chv_night_productivity) * params.asv_hours_per_quarter
    return max(hours, 0.0)


def _produce_services(clinic, quarter, out: ClinicOutcome, params, rng, referrals=None):
    """Actes demandés, plafonnés par le temps disponible de chaque réserve.

    Les actes à recrutement régional (urgences, cas référés) sont calculés sur
    le bassin régional de la clinique et non sur sa seule patientèle. Un acte
    est servi dans la limite de sa réserve vétérinaire (généralistes ou
    spécialistes de la discipline) et du temps ASV commun.
    """
    animals = out.patients["dogs"] + out.patients["cats"]
    caps = clinic_capabilities(clinic, quarter)

    low_cost = clinic.format == Clinic.Format.LOW_COST
    time_factor = params.lowcost_time_factor if low_cost else 1.0
    consumables_factor = Decimal(str(params.lowcost_consumables_factor if low_cost else 1.0))

    demanded = []  # (ClinicService, réserve, nombre d'actes demandés)
    vet_demand: dict[str, float] = {}
    asv_minutes = 0.0
    for cs in clinic.services.select_related("service_type__required_equipment"):
        if not _is_operable(clinic, cs, caps):
            continue
        st = cs.service_type
        base = (referrals or {}).get(st.code, {}).get(clinic.id, 0.0) if st.referral else animals
        volume = (
            base
            * st.frequency_per_animal
            * demand.price_volume_factor(float(cs.price), float(st.reference_price), params.service_price_elasticity)
            * _noise(rng, params)
        )
        pool = st.required_specialty or GENERAL_POOL
        demanded.append((cs, pool, volume))
        vet_demand[pool] = vet_demand.get(pool, 0.0) + volume * st.vet_minutes * time_factor / 60
        asv_minutes += volume * st.asv_minutes * time_factor

    available = vet_pools(clinic, quarter, params)
    asv_available = asv_hours(clinic, quarter, params)
    asv_ratio = min(1.0, asv_available * 60 / asv_minutes) if asv_minutes else 1.0
    pool_ratio = {
        pool: min(1.0, available.get(pool, 0.0) / hours) if hours else 1.0 for pool, hours in vet_demand.items()
    }

    revenue = consumables = ZERO
    services_detail = {}
    served_minutes = demanded_minutes = 0.0
    for cs, pool, volume in demanded:
        ratio = min(pool_ratio[pool], asv_ratio)
        demanded_minutes += volume * cs.service_type.vet_minutes
        served_minutes += volume * ratio * cs.service_type.vet_minutes
        acts = round(volume * ratio)
        if not acts:
            continue
        out.acts += acts
        revenue += cs.price * acts
        consumables += cs.service_type.consumables_cost * consumables_factor * acts
        services_detail[cs.service_type.code] = {"acts": acts, "revenue": str(money(cs.price * acts))}
    # Part de la demande servie (pondérée par le temps vétérinaire) : sert à la réputation.
    out.capacity_ratio = served_minutes / demanded_minutes if demanded_minutes else 1.0
    # Part de la patientèle propre effectivement reçue : sert aux ventes de produits.
    out.general_ratio = min(pool_ratio.get(GENERAL_POOL, 1.0), asv_ratio)
    out.details["services"] = services_detail
    out.details["workload"] = {
        "vet_hours_demanded": round(sum(vet_demand.values()), 1),
        "vet_hours_available": round(sum(available.values()), 1),
        "asv_hours_demanded": round(asv_minutes / 60, 1),
        "asv_hours_available": round(asv_available, 1),
        "pools": {
            pool: {"demanded": round(vet_demand.get(pool, 0.0), 1), "available": round(available.get(pool, 0.0), 1)}
            for pool in sorted(set(vet_demand) | set(available))
        },
    }
    post_entry(clinic, quarter, "Prestations du trimestre", [(pcg.BANK, revenue, 0), (pcg.SERVICES_REVENUE, 0, revenue)])
    post_entry(clinic, quarter, "Consommables médicaux", [(pcg.CONSUMABLES, consumables, 0), (pcg.BANK, 0, consumables)])


def _sell_products(clinic, quarter, out: ClinicOutcome, params, rng):
    """Ventes au comptoir, proportionnelles aux animaux effectivement reçus."""
    seen = {s: out.patients[s] * out.general_ratio for s in demand.SPECIES}
    revenue = ZERO
    demanded_total = sold_total = 0
    products_detail = {}
    for item in clinic.stock.select_related("product_line"):
        pl = item.product_line
        if pl.species == ProductLine.Species.DOG:
            animals = seen["dogs"]
        elif pl.species == ProductLine.Species.CAT:
            animals = seen["cats"]
        else:
            animals = seen["dogs"] + seen["cats"]
        wanted = round(
            animals
            * pl.units_per_animal
            * demand.price_volume_factor(float(item.retail_price), float(pl.recommended_price), params.product_price_elasticity)
            * _noise(rng, params)
        )
        sold = min(wanted, item.quantity)
        demanded_total += wanted
        sold_total += sold
        if sold:
            item.quantity -= sold
            item.save(update_fields=["quantity"])
            revenue += item.retail_price * sold
        products_detail[pl.code] = {"demanded": wanted, "sold": sold, "stock_end": item.quantity}
    out.stock_fill_rate = sold_total / demanded_total if demanded_total else 1.0
    out.details["products"] = products_detail
    post_entry(clinic, quarter, "Ventes de marchandises", [(pcg.BANK, revenue, 0), (pcg.GOODS_REVENUE, 0, revenue)])


def _post_fixed_charges(clinic, quarter, params):
    gross = sum((m.gross_monthly_salary * 3 for m in active_staff(clinic, quarter)), ZERO)
    post_entry(clinic, quarter, "Salaires du trimestre", [(pcg.SALARIES, gross, 0), (pcg.BANK, 0, gross)])
    if clinic.format == Clinic.Format.CHV:
        premium = chv_majorations(clinic, quarter, params)
        post_entry(
            clinic,
            quarter,
            "Majorations conventionnelles nuit, dimanche et jours fériés (permanence CHV)",
            [(pcg.SALARIES, premium, 0), (pcg.BANK, 0, premium)],
        )
        gross += premium
        extra = Decimal(str(params.chv_extra_fixed_costs_per_quarter))
        post_entry(clinic, quarter, "Fonctionnement 24 h/24 (CHV)", [(pcg.OTHER_EXTERNAL, extra, 0), (pcg.BANK, 0, extra)])
    social = money(gross * Decimal(str(params.employer_contribution_rate)))
    post_entry(clinic, quarter, "Charges sociales patronales", [(pcg.SOCIAL_CHARGES, social, 0), (pcg.BANK, 0, social)])
    post_entry(
        clinic, quarter, "Loyer des locaux", [(pcg.RENT, clinic.rent_per_quarter, 0), (pcg.BANK, 0, clinic.rent_per_quarter)]
    )
    post_entry(
        clinic,
        quarter,
        "Honoraires expert-comptable",
        [(pcg.FEES, clinic.accountant_fee_per_quarter, 0), (pcg.BANK, 0, clinic.accountant_fee_per_quarter)],
    )
    other = Decimal(str(params.other_fixed_costs_per_quarter))
    post_entry(clinic, quarter, "Autres charges externes", [(pcg.OTHER_EXTERNAL, other, 0), (pcg.BANK, 0, other)])
    leases = sum((e.lease_per_quarter for e in active_equipment(clinic, quarter).filter(mode="lease")), ZERO)
    post_entry(clinic, quarter, "Loyers de leasing", [(pcg.LEASING, leases, 0), (pcg.BANK, 0, leases)])


def permanence_fte(params) -> float:
    """ETP mobilisés par poste (vétérinaire, personne qualifiée) pour la permanence 24 h/24."""
    return params.chv_permanence_hours_per_quarter / params.ccn_hours_per_fte_quarter


def chv_majorations(clinic, quarter, params) -> Decimal:
    """Majorations conventionnelles des heures de nuit, dimanche et jours fériés.

    Un vétérinaire et une personne qualifiée sont présents sur ces heures ;
    on applique le taux de majoration au salaire horaire moyen de chaque poste.
    """
    total = ZERO
    hours = Decimal(str(params.chv_majored_hours_per_quarter))
    for role, rate in (
        (StaffMember.Role.VET, params.ccn_vet_night_majoration),
        (StaffMember.Role.ASV, params.ccn_asv_night_majoration),
    ):
        salaries = [m.gross_monthly_salary for m in active_staff(clinic, quarter).filter(role=role)]
        if salaries:
            average_hourly = ccn.hourly_rate(sum(salaries, ZERO) / len(salaries))
            total += hours * average_hourly * Decimal(str(rate))
    return money(total)


def _post_depreciation(clinic, quarter):
    """Amortissement linéaire trimestriel des immobilisations achetées (matériel, agencements)."""
    by_account: dict[str, Decimal] = {}
    for item in active_equipment(clinic, quarter).filter(mode="purchase").select_related("equipment_type"):
        remaining = item.purchase_price - item.accumulated_depreciation
        if remaining <= 0:
            continue
        dotation = min(money(item.purchase_price / item.equipment_type.useful_life_quarters), remaining)
        item.accumulated_depreciation += dotation
        item.save(update_fields=["accumulated_depreciation"])
        account = pcg.depreciation_account(item.equipment_type.asset_account)
        by_account[account] = by_account.get(account, ZERO) + dotation
    total = sum(by_account.values(), ZERO)
    post_entry(
        clinic,
        quarter,
        "Dotation aux amortissements",
        [(pcg.DEPRECIATION_EXPENSE, total, 0)] + [(account, 0, amount) for account, amount in sorted(by_account.items())],
    )


def _post_stock_variation(clinic, quarter, out: ClinicOutcome):
    """Variation de stock = stock initial - stock final (compte 6037)."""
    variation = out.stock_value_start - _stock_value(clinic)
    if variation > 0:
        lines = [(pcg.STOCK_VARIATION, variation, 0), (pcg.STOCK_GOODS, 0, variation)]
    else:
        lines = [(pcg.STOCK_GOODS, -variation, 0), (pcg.STOCK_VARIATION, 0, -variation)]
    post_entry(clinic, quarter, "Variation des stocks", lines)


def _post_overdraft_interest(clinic, quarter, params):
    clinic.refresh_from_db(fields=["cash"])
    if clinic.cash >= 0:
        return
    interest = money(-clinic.cash * Decimal(str(params.overdraft_rate_per_quarter)))
    post_entry(clinic, quarter, "Agios sur découvert", [(pcg.BANK_INTEREST, interest, 0), (pcg.BANK, 0, interest)])


def experience_index(clinic, quarter) -> float:
    """Expérience moyenne de l'équipe dans [-1, 1] (0 = échelon médian de chaque poste)."""
    scores = [ccn.experience_score(m.role, m.level) for m in active_staff(clinic, quarter)]
    return sum(scores) / len(scores) if scores else 0.0


def _update_reputation(clinic, out: ClinicOutcome, params, quarter):
    """La réputation suit la satisfaction : patients servis, produits disponibles,
    expérience de l'équipe (qualité perçue des soins)."""
    satisfaction = 0.7 * min(out.capacity_ratio, 1.0) + 0.3 * out.stock_fill_rate
    satisfaction += params.reputation_experience_weight * experience_index(clinic, quarter)
    satisfaction = max(0.0, min(1.0, satisfaction))
    clinic.reputation = params.reputation_inertia * clinic.reputation + (1 - params.reputation_inertia) * satisfaction
    clinic.save(update_fields=["reputation"])


def _build_report(clinic, quarter, out: ClinicOutcome, market_share: float) -> QuarterReport:
    clinic.refresh_from_db(fields=["cash"])
    statement = income_statement(clinic, quarter)
    report, _ = QuarterReport.objects.update_or_create(
        clinic=clinic,
        quarter=quarter,
        defaults={
            **statement,
            "patients_dogs": round(out.patients["dogs"]),
            "patients_cats": round(out.patients["cats"]),
            "acts": round(out.acts),
            "capacity_ratio": round(min(out.capacity_ratio, 1.0), 4),
            "stock_fill_rate": round(out.stock_fill_rate, 4),
            "market_share": round(market_share, 4),
            "reputation": round(clinic.reputation, 4),
            "cash_end": clinic.cash,
            "stock_value_end": _stock_value(clinic),
            "debt_end": outstanding_debt(clinic),
            "equity_end": equity(clinic),
            "clinic_format": clinic.format,
            "details": out.details,
        },
    )
    return report


def _advance(server: GameServer):
    if server.current_quarter >= server.total_quarters:
        server.status = GameServer.Status.FINISHED
        server.next_resolution_at = None
    else:
        server.current_quarter += 1
        base = server.next_resolution_at or timezone.now()
        server.next_resolution_at = base + timedelta(days=server.resolution_interval_days)
    server.save(update_fields=["status", "current_quarter", "next_resolution_at"])
