"""Formats d'établissement : clinique standard, low-cost, centre hospitalier vétérinaire.

Règles de jeu (simplifiées, paramétrables dans ``SimulationParameters``) :

- **Low-cost** : offre restreinte aux actes de base (pas d'imagerie ni de
  laboratoire, pas d'actes de CHV) ; actes standardisés plus rapides et
  consommables moins chers. Aucun coût de conversion.
- **CHV** : exige un effectif minimal de vétérinaires et d'ASV et un plateau
  technique complet (radiographie, échographie, biochimie, hématologie).
  Le premier passage en CHV impose des travaux d'aménagement immobilisés
  (2135) et amortis. Le CHV ouvre des prestations exclusives (urgences,
  hospitalisation, chirurgie spécialisée), gagne en attractivité mais
  supporte des surcoûts de permanence des soins. Les conditions sont
  vérifiées à chaque trimestre : une clinique qui ne les remplit plus perd
  son statut.

Les seuils réglementaires réels (arrêté relatif aux catégories
d'établissements de soins vétérinaires) sont à valider par l'équipe
pédagogique ; ils se règlent via les paramètres ``chv_min_vets`` et
``chv_min_asv``.
"""

from .models import Clinic, EquipmentType, StaffMember

Format = Clinic.Format

CHV_REQUIRED_EQUIPMENT = ("radio-numerique", "echographe", "lab-biochimie", "lab-hemato")
CHV_FITOUT_CODE = "amenagement-chv"

DESCRIPTIONS = {
    Format.STANDARD: "Offre complète selon votre matériel, sans contrainte particulière.",
    Format.LOW_COST: "Actes de base uniquement (consultation, vaccination, stérilisation, détartrage). "
    "Protocoles standardisés : temps par acte réduit, consommables moins chers.",
    Format.CHV: "Urgences 24 h/24, hospitalisation, chirurgie spécialisée. Plus attractif, "
    "mais exige un effectif et un plateau technique minimum, et coûte cher à faire tourner.",
}


def unavailability_reason(clinic_format: str, service_type, owned_equipment_ids: set) -> str | None:
    """Pourquoi une prestation ne peut pas être proposée (``None`` si elle le peut)."""
    if service_type.required_format and service_type.required_format != clinic_format:
        return f"réservée au format {Format(service_type.required_format).label}"
    if clinic_format == Format.LOW_COST and service_type.required_equipment_id:
        return "hors offre low-cost"
    eq = service_type.required_equipment_id
    if eq and eq not in owned_equipment_ids:
        return f"nécessite : {service_type.required_equipment.name}"
    return None


def chv_requirements(vets: int, asvs: int, owned_codes: set, params) -> list[tuple[str, bool]]:
    names = dict(EquipmentType.objects.filter(code__in=CHV_REQUIRED_EQUIPMENT).values_list("code", "name"))
    rows = [
        (f"Au moins {int(params.chv_min_vets)} vétérinaires (actuellement {vets})", vets >= params.chv_min_vets),
        (f"Au moins {int(params.chv_min_asv)} ASV (actuellement {asvs})", asvs >= params.chv_min_asv),
    ]
    rows += [(names.get(code, code), code in owned_codes) for code in CHV_REQUIRED_EQUIPMENT]
    return rows


def current_chv_requirements(clinic, quarter: int, params, durable: bool = False) -> list[tuple[str, bool]]:
    """Conditions du CHV dans l'état actuel.

    ``durable=True`` (passage au format) : on ne compte ni les salariés sur le
    départ ni le matériel dont le leasing est résilié. Sinon (contrôle de
    début de trimestre) : on compte ceux qui travaillent encore ce trimestre.
    """
    from .services import active_equipment, active_staff

    staff = active_staff(clinic, quarter)
    equipment = active_equipment(clinic, quarter)
    if durable:
        staff = staff.filter(left_quarter__isnull=True)
        equipment = equipment.filter(released_quarter__isnull=True)
    owned = set(equipment.values_list("equipment_type__code", flat=True))
    return chv_requirements(
        staff.filter(role=StaffMember.Role.VET).count(), staff.filter(role=StaffMember.Role.ASV).count(), owned, params
    )


def missing_chv_requirements(clinic, quarter: int, params, durable: bool = False) -> list[str]:
    return [label for label, ok in current_chv_requirements(clinic, quarter, params, durable) if not ok]


def has_chv_fitout(clinic) -> bool:
    return clinic.equipment.filter(equipment_type__code=CHV_FITOUT_CODE).exists()


def sync_services(clinic, quarter: int):
    """Désactive les prestations devenues impossibles et active celles réservées au format."""
    from .services import active_equipment

    owned = set(active_equipment(clinic, quarter).values_list("equipment_type_id", flat=True))
    for cs in clinic.services.select_related("service_type__required_equipment"):
        reason = unavailability_reason(clinic.format, cs.service_type, owned)
        if reason and cs.active:
            cs.active = False
            cs.save(update_fields=["active"])
        elif not reason and cs.service_type.required_format and not cs.active:
            cs.active = True  # les actes de CHV sont proposés dès l'obtention du statut
            cs.save(update_fields=["active"])


def set_format(clinic, new_format: str, params) -> str:
    from .services import OperationError, capitalize_asset

    if new_format not in Format.values:
        raise OperationError(f"Format inconnu : {new_format}")
    if new_format == clinic.format:
        raise OperationError(f"La clinique est déjà au format {Format(new_format).label}.")
    quarter = clinic.server.current_quarter
    if new_format == Format.CHV:
        missing = missing_chv_requirements(clinic, quarter, params, durable=True)
        if missing:
            raise OperationError("Conditions CHV non remplies : " + " ; ".join(missing))
        if not has_chv_fitout(clinic):
            capitalize_asset(clinic, EquipmentType.objects.get(code=CHV_FITOUT_CODE))
    clinic.format = new_format
    clinic.save(update_fields=["format"])
    sync_services(clinic, quarter)
    return f"Format : {clinic.get_format_display()}"


def enforce_chv_status(clinic, quarter: int, params) -> str | None:
    """Rétrograde un CHV qui ne remplit plus les conditions ; renvoie l'événement éventuel."""
    if clinic.format != Format.CHV:
        return None
    missing = missing_chv_requirements(clinic, quarter, params)
    if not missing:
        return None
    clinic.format = Format.STANDARD
    clinic.save(update_fields=["format"])
    sync_services(clinic, quarter)
    return "Perte du statut CHV (conditions non remplies : " + " ; ".join(missing) + ")."


