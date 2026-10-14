"""Application des décisions en attente lors de la résolution d'un trimestre."""

from decimal import Decimal, InvalidOperation

from django.db import transaction
from django.utils import timezone

from clinics import services as clinic_services
from clinics.services import OperationError

from .models import PendingDecision


def _decimal(payload, key, required=True):
    if key not in payload:
        if required:
            raise OperationError(f"Champ manquant : {key}")
        return None
    try:
        value = Decimal(str(payload[key]))
    except InvalidOperation:
        raise OperationError(f"Valeur invalide pour {key}")
    if value < 0:
        raise OperationError(f"{key} doit être positif")
    return value


def _set_service(clinic, payload, params):
    cs = clinic.services.select_related("service_type").filter(service_type__code=payload.get("service")).first()
    if cs is None:
        raise OperationError("Prestation inconnue.")
    price = _decimal(payload, "price", required=False)
    if price is not None:
        cs.price = price
    if "active" in payload:
        active = bool(payload["active"])
        eq = cs.service_type.required_equipment
        if active and eq and not clinic.equipment.filter(equipment_type=eq, released_quarter__isnull=True).exists():
            raise OperationError(f"Cette prestation nécessite : {eq.name}.")
        cs.active = active
    cs.save()
    return f"{cs.service_type.name} : {cs.price} € HT, {'active' if cs.active else 'inactive'}"


def _set_product(clinic, payload, params):
    item = clinic.stock.select_related("product_line").filter(product_line__code=payload.get("product")).first()
    if item is None:
        raise OperationError("Gamme inconnue.")
    price = _decimal(payload, "retail_price", required=False)
    if price is not None:
        item.retail_price = price
    if "target_quantity" in payload:
        target = int(payload["target_quantity"])
        if target < 0:
            raise OperationError("Le niveau de réassort doit être positif.")
        item.target_quantity = target
    item.save()
    return f"{item.product_line.name} : {item.retail_price} € HT, réassort à {item.target_quantity}"


def _hire(clinic, payload, params):
    member = clinic_services.hire(
        clinic, payload.get("role"), payload.get("name", "Nouveau salarié"), _decimal(payload, "gross_monthly_salary")
    )
    return f"Recrutement : {member}"


def _dismiss(clinic, payload, params):
    member = clinic_services.dismiss(clinic, int(payload.get("staff_id", 0)), params.severance_months)
    return f"Départ : {member}"


def _acquire(clinic, payload, params):
    item = clinic_services.acquire_equipment(clinic, payload.get("equipment"), payload.get("mode", "lease"))
    return f"Matériel : {item}"


def _release(clinic, payload, params):
    item = clinic_services.release_equipment(clinic, int(payload.get("clinic_equipment_id", 0)))
    return f"Fin de leasing : {item}"


HANDLERS = {
    PendingDecision.Kind.SET_SERVICE: _set_service,
    PendingDecision.Kind.SET_PRODUCT: _set_product,
    PendingDecision.Kind.HIRE: _hire,
    PendingDecision.Kind.DISMISS: _dismiss,
    PendingDecision.Kind.ACQUIRE_EQUIPMENT: _acquire,
    PendingDecision.Kind.RELEASE_EQUIPMENT: _release,
}


def apply_pending_decisions(clinic, quarter: int, params) -> list[PendingDecision]:
    """Applique dans l'ordre chronologique les décisions du trimestre.

    Une décision invalide est rejetée avec un message, sans bloquer les autres.
    """
    decisions = list(clinic.decisions.filter(quarter=quarter, status=PendingDecision.Status.PENDING))
    now = timezone.now()
    for decision in decisions:
        handler = HANDLERS.get(decision.kind)
        try:
            if handler is None:
                raise OperationError("Type de décision inconnu.")
            # Point de sauvegarde : une décision rejetée ne laisse aucune trace partielle.
            with transaction.atomic():
                decision.message = handler(clinic, decision.payload, params)[:255]
            decision.status = PendingDecision.Status.APPLIED
        except (OperationError, ValueError, TypeError) as exc:
            decision.message = str(exc)[:255]
            decision.status = PendingDecision.Status.REJECTED
        decision.resolved_at = now
        decision.save(update_fields=["message", "status", "resolved_at"])
    return decisions
