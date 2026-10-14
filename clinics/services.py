"""Opérations métier sur une clinique (création, personnel, matériel).

Toutes les opérations ayant un impact financier passent par
``accounting.services.post_entry`` pour garantir la cohérence comptable.
"""

from decimal import Decimal

from django.db import transaction

from accounting import pcg
from accounting.services import money, post_entry
from inventory.models import ProductLine, StockItem

from .models import Clinic, ClinicEquipment, ClinicService, EquipmentType, ServiceType, StaffMember

DEFAULT_VET_SALARY = Decimal("4000")
DEFAULT_ASV_SALARY = Decimal("1900")
MIN_GROSS_MONTHLY_SALARY = Decimal("1800")  # minimum conventionnel simulé


class OperationError(ValueError):
    """Opération refusée (règle métier non respectée)."""


@transaction.atomic
def create_clinic(
    server,
    owner,
    name: str,
    x: int,
    y: int,
    initial_capital: Decimal = Decimal("80000"),
    with_default_staff: bool = True,
) -> Clinic:
    if not (0 <= x < server.grid_width and 0 <= y < server.grid_height):
        raise OperationError("Position hors de la carte.")
    if server.clinics.filter(x=x, y=y).exists():
        raise OperationError("Une clinique occupe déjà cette case.")

    clinic = Clinic.objects.create(server=server, owner=owner, name=name, x=x, y=y)
    quarter = server.current_quarter
    post_entry(
        clinic,
        quarter,
        "Apport en capital",
        [(pcg.BANK, initial_capital, 0), (pcg.CAPITAL, 0, initial_capital)],
    )
    for st in ServiceType.objects.all():
        ClinicService.objects.create(
            clinic=clinic,
            service_type=st,
            price=st.reference_price,
            # Les actes nécessitant du matériel restent inactifs tant qu'il n'est pas acquis.
            active=st.required_equipment_id is None,
        )
    for pl in ProductLine.objects.all():
        StockItem.objects.create(clinic=clinic, product_line=pl, retail_price=pl.recommended_price)
    if with_default_staff:
        hire(clinic, StaffMember.Role.VET, "Vétérinaire fondateur", DEFAULT_VET_SALARY)
        hire(clinic, StaffMember.Role.ASV, "ASV", DEFAULT_ASV_SALARY)
    return clinic


def hire(clinic: Clinic, role: str, name: str, gross_monthly_salary: Decimal) -> StaffMember:
    if role not in StaffMember.Role.values:
        raise OperationError(f"Rôle inconnu : {role}")
    if Decimal(gross_monthly_salary) < MIN_GROSS_MONTHLY_SALARY:
        raise OperationError(f"Salaire inférieur au minimum conventionnel simulé ({MIN_GROSS_MONTHLY_SALARY} € brut).")
    return StaffMember.objects.create(
        clinic=clinic,
        role=role,
        name=name,
        gross_monthly_salary=money(gross_monthly_salary),
        hired_quarter=clinic.server.current_quarter,
    )


def dismiss(clinic: Clinic, staff_id: int, severance_months: float) -> StaffMember:
    member = clinic.staff.filter(pk=staff_id, left_quarter__isnull=True).first()
    if member is None:
        raise OperationError("Salarié introuvable ou déjà parti.")
    quarter = clinic.server.current_quarter
    # Simplification : le salarié quitte la clinique en fin de trimestre (préavis),
    # son salaire du trimestre reste dû.
    member.left_quarter = quarter
    member.save(update_fields=["left_quarter"])
    severance = money(member.gross_monthly_salary * Decimal(str(severance_months)))
    post_entry(
        clinic,
        quarter,
        f"Indemnité de rupture – {member.name}",
        [(pcg.SEVERANCE, severance, 0), (pcg.BANK, 0, severance)],
    )
    return member


def acquire_equipment(clinic: Clinic, equipment_code: str, mode: str) -> ClinicEquipment:
    eq_type = EquipmentType.objects.filter(code=equipment_code).first()
    if eq_type is None:
        raise OperationError(f"Matériel inconnu : {equipment_code}")
    if mode not in ClinicEquipment.Mode.values:
        raise OperationError(f"Mode d'acquisition inconnu : {mode}")
    if clinic.equipment.filter(equipment_type=eq_type, released_quarter__isnull=True).exists():
        raise OperationError("La clinique possède déjà ce matériel.")

    quarter = clinic.server.current_quarter
    item = ClinicEquipment(clinic=clinic, equipment_type=eq_type, mode=mode, acquired_quarter=quarter)
    if mode == ClinicEquipment.Mode.PURCHASE:
        item.purchase_price = eq_type.purchase_price
        item.save()
        post_entry(
            clinic,
            quarter,
            f"Achat {eq_type.name}",
            [(pcg.EQUIPMENT, eq_type.purchase_price, 0), (pcg.BANK, 0, eq_type.purchase_price)],
        )
    else:
        item.lease_per_quarter = eq_type.lease_per_quarter
        item.save()
    # Activer automatiquement les prestations rendues possibles par ce matériel.
    clinic.services.filter(service_type__required_equipment=eq_type).update(active=True)
    return item


def release_equipment(clinic: Clinic, clinic_equipment_id: int) -> ClinicEquipment:
    item = clinic.equipment.filter(pk=clinic_equipment_id, released_quarter__isnull=True).first()
    if item is None:
        raise OperationError("Matériel introuvable.")
    if item.mode != ClinicEquipment.Mode.LEASE:
        raise OperationError("Seul un contrat de leasing peut être résilié (cession non gérée en V1).")
    # Le loyer du trimestre en cours reste dû : fin effective au trimestre suivant.
    item.released_quarter = clinic.server.current_quarter + 1
    item.save(update_fields=["released_quarter"])
    return item


def active_staff(clinic: Clinic, quarter: int):
    """Personnel payé et productif pendant ``quarter``."""
    return clinic.staff.filter(hired_quarter__lte=quarter).exclude(left_quarter__lt=quarter)


def active_equipment(clinic: Clinic, quarter: int):
    return clinic.equipment.filter(acquired_quarter__lte=quarter).exclude(released_quarter__lte=quarter)
