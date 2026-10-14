"""Résolution d'un trimestre de jeu (tâche hebdomadaire).

Ordre des étapes pour un serveur :

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
from clinics.formats import enforce_chv_status, unavailability_reason
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

    for clinic in clinics:
        apply_pending_decisions(clinic, quarter, params)
        clinic.refresh_from_db()
        event = enforce_chv_status(clinic, quarter, params)
        outcomes[clinic.id].details["events"] = [event] if event else []
        outcomes[clinic.id].stock_value_start = _stock_value(clinic)
        _restock(clinic, quarter, params)

    patients = _allocate_market(server, clinics, params)
    total_patients = sum(p["dogs"] + p["cats"] for p in patients.values()) or 1.0

    reports = []
    for clinic in clinics:
        out = outcomes[clinic.id]
        out.patients = patients[clinic.id]
        rng = random.Random(f"{server.seed}-{quarter}-{clinic.x}-{clinic.y}")
        _produce_services(clinic, quarter, out, params, rng)
        _sell_products(clinic, quarter, out, params, rng)
        _post_fixed_charges(clinic, quarter, params)
        pay_installments(clinic, quarter)
        _post_depreciation(clinic, quarter)
        _post_stock_variation(clinic, quarter, out)
        _post_overdraft_interest(clinic, quarter, params)
        _update_reputation(clinic, out, params)
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

    Calculés sur l'offre généraliste : les actes réservés aux CHV n'entrent pas
    dans le panier (leur effet passe par le bonus d'attractivité du format).
    """
    available_equipment = set(active_equipment(clinic, quarter).values_list("equipment_type_id", flat=True))
    weighted_price = weight_offered = weight_total = 0.0
    for cs in clinic.services.select_related("service_type").filter(service_type__required_format=""):
        st = cs.service_type
        w = st.market_weight
        weight_total += w
        if _is_operable(clinic, cs, available_equipment):
            weight_offered += w
            weighted_price += w * float(cs.price) / float(st.reference_price)
    price_index = weighted_price / weight_offered if weight_offered else 1.0
    offer_ratio = weight_offered / weight_total if weight_total else 0.0
    return price_index, offer_ratio


def _is_operable(clinic, cs, available_equipment: set) -> bool:
    return cs.active and unavailability_reason(clinic.format, cs.service_type, available_equipment) is None


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


def _noise(rng: random.Random, params: SimulationParameters) -> float:
    return 1.0 + rng.uniform(-params.noise, params.noise)


def _produce_services(clinic, quarter, out: ClinicOutcome, params, rng):
    """Actes demandés par la patientèle, plafonnés par le temps disponible."""
    animals = out.patients["dogs"] + out.patients["cats"]
    available_equipment = set(active_equipment(clinic, quarter).values_list("equipment_type_id", flat=True))

    low_cost = clinic.format == Clinic.Format.LOW_COST
    time_factor = params.lowcost_time_factor if low_cost else 1.0
    consumables_factor = Decimal(str(params.lowcost_consumables_factor if low_cost else 1.0))

    demanded = []  # (ClinicService, nombre d'actes demandés)
    vet_minutes = asv_minutes = 0.0
    for cs in clinic.services.select_related("service_type__required_equipment"):
        if not _is_operable(clinic, cs, available_equipment):
            continue
        st = cs.service_type
        volume = (
            animals
            * st.frequency_per_animal
            * demand.price_volume_factor(float(cs.price), float(st.reference_price), params.service_price_elasticity)
            * _noise(rng, params)
        )
        demanded.append((cs, volume))
        vet_minutes += volume * st.vet_minutes * time_factor
        asv_minutes += volume * st.asv_minutes * time_factor

    staff = active_staff(clinic, quarter)
    vet_capacity = staff.filter(role=StaffMember.Role.VET).count() * params.vet_hours_per_quarter * 60
    asv_capacity = staff.filter(role=StaffMember.Role.ASV).count() * params.asv_hours_per_quarter * 60
    ratios = [1.0]
    if vet_minutes:
        ratios.append(vet_capacity / vet_minutes)
    if asv_minutes:
        ratios.append(asv_capacity / asv_minutes)
    out.capacity_ratio = min(ratios)

    revenue = consumables = ZERO
    services_detail = {}
    for cs, volume in demanded:
        acts = round(volume * out.capacity_ratio)
        if not acts:
            continue
        out.acts += acts
        revenue += cs.price * acts
        consumables += cs.service_type.consumables_cost * consumables_factor * acts
        services_detail[cs.service_type.code] = {"acts": acts, "revenue": str(money(cs.price * acts))}
    out.details["services"] = services_detail
    out.details["workload"] = {
        "vet_hours_demanded": round(vet_minutes / 60, 1),
        "vet_hours_available": round(vet_capacity / 60, 1),
        "asv_hours_demanded": round(asv_minutes / 60, 1),
        "asv_hours_available": round(asv_capacity / 60, 1),
    }
    post_entry(clinic, quarter, "Prestations du trimestre", [(pcg.BANK, revenue, 0), (pcg.SERVICES_REVENUE, 0, revenue)])
    post_entry(clinic, quarter, "Consommables médicaux", [(pcg.CONSUMABLES, consumables, 0), (pcg.BANK, 0, consumables)])


def _sell_products(clinic, quarter, out: ClinicOutcome, params, rng):
    """Ventes au comptoir, proportionnelles aux animaux effectivement reçus."""
    seen = {s: out.patients[s] * out.capacity_ratio for s in demand.SPECIES}
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
        premium = money(gross * Decimal(str(params.chv_night_premium_rate)))
        post_entry(clinic, quarter, "Majorations nuit, dimanche et garde (CHV)", [(pcg.SALARIES, premium, 0), (pcg.BANK, 0, premium)])
        gross += premium
        extra = Decimal(str(params.chv_extra_fixed_costs_per_quarter))
        post_entry(clinic, quarter, "Permanence des soins 24 h/24 (CHV)", [(pcg.OTHER_EXTERNAL, extra, 0), (pcg.BANK, 0, extra)])
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


def _update_reputation(clinic, out: ClinicOutcome, params):
    """La réputation suit la satisfaction : patients servis et produits disponibles."""
    satisfaction = 0.7 * min(out.capacity_ratio, 1.0) + 0.3 * out.stock_fill_rate
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
