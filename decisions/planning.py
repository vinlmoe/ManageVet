"""Vue « planifiée » d'une clinique : l'état actuel + les décisions en attente.

L'écran joueur affiche les valeurs telles qu'elles seront après application
des décisions du trimestre, et une nouvelle saisie *remplace* la décision en
attente portant sur le même objet (pas d'empilement de décisions).
"""

from decimal import Decimal

from django.db import transaction

from clinics.models import StaffMember

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
    from clinics.models import EquipmentType, ServiceType
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
        return f"Recrutement : {p.get('name')} ({role}), {salary} € brut/mois", -cost.quantize(Decimal("1"))
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
    return decision.get_kind_display(), None
