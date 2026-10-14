"""Vue « planifiée » d'une clinique : l'état actuel + les décisions en attente.

L'écran joueur affiche les valeurs telles qu'elles seront après application
des décisions du trimestre, et une nouvelle saisie *remplace* la décision en
attente portant sur le même objet (pas d'empilement de décisions).
"""

from decimal import Decimal

from django.db import transaction

from clinics import ccn, formats
from clinics.models import Clinic, EquipmentType, Specialty, StaffMember

from .models import PendingDecision

Kind = PendingDecision.Kind


def pending(clinic, quarter: int):
    return clinic.decisions.filter(quarter=quarter, status=PendingDecision.Status.PENDING)


def planned_services(clinic, quarter: int) -> dict[str, dict]:
    plan = {
        cs.service_type.code: {"price": cs.price, "active": cs.active}
        for cs in clinic.services.select_related("service_type")
    }
    for d in pending(clinic, quarter).filter(kind=Kind.SET_SERVICE):
        row = plan.get(d.payload.get("service"))
        if row is None:
            continue
        if "price" in d.payload:
            row["price"] = Decimal(str(d.payload["price"]))
        if "active" in d.payload:
            row["active"] = bool(d.payload["active"])
    return plan


def planned_products(clinic, quarter: int) -> dict[str, dict]:
    plan = {
        item.product_line.code: {"retail_price": item.retail_price, "target_quantity": item.target_quantity}
        for item in clinic.stock.select_related("product_line")
    }
    for d in pending(clinic, quarter).filter(kind=Kind.SET_PRODUCT):
        row = plan.get(d.payload.get("product"))
        if row is None:
            continue
        if "retail_price" in d.payload:
            row["retail_price"] = Decimal(str(d.payload["retail_price"]))
        if "target_quantity" in d.payload:
            row["target_quantity"] = int(d.payload["target_quantity"])
    return plan


@transaction.atomic
def replace_pending(clinic, quarter: int, kind: str, key: str, value, payload: dict | None) -> PendingDecision | None:
    """Annule les décisions en attente de même type portant sur ``payload[key] == value``,
    puis enregistre ``payload`` (si non ``None``)."""
    for d in pending(clinic, quarter).filter(kind=kind):
        if d.payload.get(key) == value:
            d.status = PendingDecision.Status.CANCELLED
            d.message = "Remplacée par une saisie plus récente."
            d.save(update_fields=["status", "message"])
    if payload is None:
        return None
    return PendingDecision.objects.create(clinic=clinic, quarter=quarter, kind=kind, payload=payload)


def describe(decision: PendingDecision, params) -> tuple[str, Decimal | None]:
    """Libellé lisible d'une décision et son impact estimé sur la trésorerie du trimestre."""
    from clinics.models import ServiceType
    from inventory.models import ProductLine

    p = decision.payload
    k = decision.kind
    if k == Kind.SET_SERVICE:
        st = ServiceType.objects.filter(code=p.get("service")).first()
        parts = []
        if "price" in p:
            parts.append(f"{p['price']} € HT")
        if "active" in p:
            parts.append("proposée" if p["active"] else "retirée de l'offre")
        return f"Prestation « {st.name if st else p.get('service')} » : {', '.join(parts)}", None
    if k == Kind.SET_PRODUCT:
        pl = ProductLine.objects.filter(code=p.get("product")).first()
        return (
            f"Gamme « {pl.name if pl else p.get('product')} » : {p.get('retail_price')} € HT, "
            f"réassort à {p.get('target_quantity')} unités",
            None,
        )
    if k == Kind.HIRE:
        salary = Decimal(str(p.get("gross_monthly_salary", 0)))
        cost = salary * 3 * (1 + Decimal(str(params.employer_contribution_rate)))
        role = StaffMember.Role(p.get("role")).label if p.get("role") in StaffMember.Role.values else p.get("role")
        if p.get("specialty") in Specialty.values:
            role = f"spécialiste en {Specialty(p['specialty']).label.lower()}"
        level = f", {ccn.level_label(p.get('role'), int(p['level']))}" if p.get("level") else ""
        return f"Recrutement : {p.get('name')} ({role}{level}), {salary} € brut/mois", -cost.quantize(Decimal("1"))
    if k == Kind.DISMISS:
        member = StaffMember.objects.filter(pk=p.get("staff_id")).first()
        cost = member.gross_monthly_salary * Decimal(str(params.severance_months)) if member else Decimal("0")
        return f"Départ : {member or 'salarié inconnu'} (indemnité, salaire du trimestre dû)", -cost.quantize(Decimal("1"))
    if k == Kind.ACQUIRE_EQUIPMENT:
        eq = EquipmentType.objects.filter(code=p.get("equipment")).first()
        if eq is None:
            return f"Matériel inconnu : {p.get('equipment')}", None
        if p.get("mode") == "purchase":
            return f"Achat : {eq.name} (amorti sur {eq.useful_life_quarters} trimestres)", -eq.purchase_price
        return f"Leasing : {eq.name}", -eq.lease_per_quarter
    if k == Kind.RELEASE_EQUIPMENT:
        return "Résiliation d'un leasing (effective au trimestre suivant)", None
    if k == Kind.SET_FORMAT:
        target = p.get("format")
        label = Clinic.Format(target).label if target in Clinic.Format.values else target
        cost = None
        if target == Clinic.Format.CHV and not formats.has_chv_fitout(decision.clinic):
            fitout = EquipmentType.objects.filter(code=formats.CHV_FITOUT_CODE).first()
            cost = -fitout.purchase_price if fitout else None
            label += " (travaux d'aménagement)"
        return f"Changement de format : {label}", cost
    if k == Kind.BORROW:
        amount = Decimal(str(p.get("amount", 0)))
        years = int(p.get("duration_quarters", 0)) / 4
        return f"Emprunt de {amount:.0f} € sur {years:g} an{'s' if years > 1 else ''} (taux fixé au déblocage)", amount
    if k == Kind.REPAY_LOAN:
        from accounting.loans import early_repayment_penalty
        from accounting.models import Loan

        loan = Loan.objects.filter(pk=p.get("loan_id"), clinic=decision.clinic).first()
        if loan is None:
            return "Remboursement anticipé : emprunt inconnu", None
        penalty = early_repayment_penalty(loan, params)
        return (
            f"Remboursement anticipé de l'emprunt de {loan.principal:.0f} € (indemnité {penalty:.0f} €)",
            -(loan.remaining_principal + penalty),
        )
    return decision.get_kind_display(), None


def planned_format(clinic, quarter: int) -> str:
    d = pending(clinic, quarter).filter(kind=Kind.SET_FORMAT).order_by("-created_at", "-id").first()
    return d.payload.get("format") if d else clinic.format


def planned_chv_requirements(clinic, quarter: int, params) -> list[tuple[str, bool]]:
    """Conditions du CHV une fois appliquées les décisions en attente (recrutements, départs, matériel)."""
    from clinics.services import active_equipment

    decisions = list(pending(clinic, quarter))
    leaving = {d.payload.get("staff_id") for d in decisions if d.kind == Kind.DISMISS}
    staying = clinic.staff.filter(left_quarter__isnull=True).exclude(pk__in=leaving)
    hires = [d.payload for d in decisions if d.kind == Kind.HIRE]
    hired_roles = [h.get("role") for h in hires]
    vets = staying.filter(role=StaffMember.Role.VET).count() + hired_roles.count(StaffMember.Role.VET)
    asvs = staying.filter(role=StaffMember.Role.ASV).count() + hired_roles.count(StaffMember.Role.ASV)
    specialties = list(staying.exclude(specialty="").values_list("specialty", flat=True))
    specialties += [h["specialty"] for h in hires if h.get("specialty")]

    released = {d.payload.get("clinic_equipment_id") for d in decisions if d.kind == Kind.RELEASE_EQUIPMENT}
    owned = set(
        active_equipment(clinic, quarter)
        .filter(released_quarter__isnull=True)
        .exclude(pk__in=released)
        .values_list("equipment_type__code", flat=True)
    )
    owned |= {d.payload.get("equipment") for d in decisions if d.kind == Kind.ACQUIRE_EQUIPMENT}
    return formats.chv_requirements(vets, asvs, specialties, owned, params)
