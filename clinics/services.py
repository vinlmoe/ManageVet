"""Opérations métier sur une clinique (création, personnel, matériel).

Toutes les opérations ayant un impact financier passent par
``accounting.services.post_entry`` pour garantir la cohérence comptable.
"""

from decimal import Decimal

from django.db import transaction

from accounting import pcg
from accounting.services import money, post_entry
from game.placement import rent_for_cell
from inventory.models import ProductLine, StockItem

from . import ccn
from .formats import activate_enabled_services
from .models import Clinic, ClinicEquipment, ClinicService, EquipmentType, ServiceType, Specialty, StaffMember

# Équipe de départ : un vétérinaire fondateur (échelon 3) et une ASV titrée (échelon 5),
# rémunérés un peu au-dessus des minima conventionnels 2026.
DEFAULT_VET_SALARY = Decimal("4000")
DEFAULT_ASV_SALARY = Decimal("2200")


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
    rent_per_quarter: Decimal | None = None,
) -> Clinic:
    if not (0 <= x < server.grid_width and 0 <= y < server.grid_height):
        raise OperationError("Position hors de la carte.")
    if server.clinics.filter(x=x, y=y).exists():
        raise OperationError("Une clinique occupe déjà cette case.")
    if server.clinics.filter(owner=owner).exists():
        raise OperationError("Ce joueur dirige déjà une clinique sur ce serveur.")

    if rent_per_quarter is None:
        rent_per_quarter = rent_for_cell(server, x, y)
    clinic = Clinic.objects.create(
        server=server, owner=owner, name=name, x=x, y=y, rent_per_quarter=rent_per_quarter
    )
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
            # Les actes nécessitant du matériel (ou un format particulier) restent
            # inactifs tant que la condition n'est pas remplie.
            active=st.required_equipment_id is None and not st.required_format and not st.required_specialty,
        )
    for pl in ProductLine.objects.all():
        StockItem.objects.create(clinic=clinic, product_line=pl, retail_price=pl.recommended_price)
    if with_default_staff:
        hire(clinic, StaffMember.Role.VET, "Vétérinaire fondateur", DEFAULT_VET_SALARY)
        hire(clinic, StaffMember.Role.ASV, "ASV", DEFAULT_ASV_SALARY)
    return clinic


def hire(
    clinic: Clinic,
    role: str,
    name: str,
    gross_monthly_salary: Decimal,
    level: int | None = None,
    specialty: str = "",
) -> StaffMember:
    """Recrute un salarié en respectant la grille de la convention collective.

    Recrutement direct (création de clinique, démo) : la difficulté de
    recrutement s'applique en amont, lors de la résolution des décisions
    (voir ``clinics.recruitment``).
    """
    level = check_offer(clinic, role, level, specialty, gross_monthly_salary)
    member = StaffMember.objects.create(
        clinic=clinic,
        role=role,
        name=name,
        level=level,
        specialty=specialty,
        gross_monthly_salary=money(gross_monthly_salary),
        hired_quarter=clinic.server.current_quarter,
    )
    if specialty:
        # Les actes de la spécialité deviennent possibles : on les propose d'office.
        activate_enabled_services(clinic, clinic.server.current_quarter, service_type__required_specialty=specialty)
    return member


def check_offer(clinic: Clinic, role: str, level, specialty: str, gross_monthly_salary) -> int:
    """Valide une offre d'emploi (poste, échelon, spécialité, minimum conventionnel) ; renvoie l'échelon."""
    if role not in StaffMember.Role.values:
        raise OperationError(f"Rôle inconnu : {role}")
    level = int(level) if level is not None else ccn.DEFAULT_LEVEL[role]
    if level not in ccn.LEVELS[role]:
        raise OperationError(f"Échelon {level} non prévu pour ce poste.")
    if specialty:
        if role != StaffMember.Role.VET:
            raise OperationError("Seul un vétérinaire peut être spécialiste.")
        if specialty not in Specialty.values:
            raise OperationError(f"Spécialité inconnue : {specialty}")
        if level != ccn.SPECIALIST_LEVEL:
            raise OperationError("Un vétérinaire spécialiste relève de l'échelon 5 (cadre spécialisé).")
    minimum = ccn.minimum_salary(role, level, clinic.server.get_parameters().ccn_point_value)
    if Decimal(gross_monthly_salary) < minimum:
        raise OperationError(
            f"Salaire inférieur au minimum conventionnel : {minimum} € brut pour « {ccn.level_label(role, level)} »."
        )
    return level


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
    if not eq_type.purchasable:
        raise OperationError(f"{eq_type.name} n'est pas proposé au catalogue.")
    if clinic.equipment.filter(equipment_type=eq_type, released_quarter__isnull=True).exists():
        raise OperationError("La clinique possède déjà ce matériel.")

    if mode == ClinicEquipment.Mode.PURCHASE:
        item = capitalize_asset(clinic, eq_type)
    else:
        item = ClinicEquipment.objects.create(
            clinic=clinic,
            equipment_type=eq_type,
            mode=mode,
            acquired_quarter=clinic.server.current_quarter,
            lease_per_quarter=eq_type.lease_per_quarter,
        )
    # Activer automatiquement les prestations rendues possibles par ce matériel
    # (si le format de la clinique et les spécialistes présents les autorisent).
    activate_enabled_services(clinic, clinic.server.current_quarter, service_type__required_equipment=eq_type)
    return item


def capitalize_asset(clinic: Clinic, eq_type: EquipmentType) -> ClinicEquipment:
    """Achat d'une immobilisation : décaissement et inscription à l'actif (2154, 2135…)."""
    quarter = clinic.server.current_quarter
    item = ClinicEquipment.objects.create(
        clinic=clinic,
        equipment_type=eq_type,
        mode=ClinicEquipment.Mode.PURCHASE,
        acquired_quarter=quarter,
        purchase_price=eq_type.purchase_price,
    )
    post_entry(
        clinic,
        quarter,
        f"Achat {eq_type.name}",
        [(eq_type.asset_account, eq_type.purchase_price, 0), (pcg.BANK, 0, eq_type.purchase_price)],
    )
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
