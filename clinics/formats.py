"""Formats d'établissement : clinique standard, low-cost, centre hospitalier vétérinaire.

Règles de jeu (paramétrables dans ``SimulationParameters``) :

- **Low-cost** : offre restreinte aux actes de base (pas d'imagerie, de
  laboratoire, d'actes de spécialiste ni d'actes de CHV) ; actes standardisés
  plus rapides et consommables moins chers. Aucun coût de conversion.
- **CHV** : conditions inspirées de l'arrêté du 13 mars 2015 relatif aux
  catégories d'établissements de soins vétérinaires et du cahier des charges
  « CHV pour animaux de compagnie » de l'Ordre (24/03/2016) :

  * au moins 6 vétérinaires et 6 personnes qualifiées (ASV titrées,
    échelon 5) à temps plein ;
  * un vétérinaire et une personne qualifiée présents 24 h/24, 7 j/7 ;
  * au moins 6 vétérinaires spécialistes (art. R. 242-34 CRPM), tous à
    l'échelon 5 (cadre spécialisé), dans au moins 2 disciplines parmi
    médecine interne, chirurgie et imagerie. Leur activité est restreinte
    aux actes de leur spécialité, mais ils reçoivent les cas référés que les
    autres cliniques ne peuvent pas prendre en charge ;
  * au moins trois techniques d'imagerie (radiographie, échographie,
    scanner, IRM) ;
  * un laboratoire : biochimie, hématologie, ionogrammes.

  Le premier passage en CHV impose des travaux d'aménagement immobilisés
  (2135) et amortis. Le CHV ouvre les urgences et l'hospitalisation, gagne en
  attractivité, mais supporte le coût de la permanence (majorations
  conventionnelles de nuit, dimanche et jours fériés, personnel mobilisé la
  nuit). Les conditions sont revérifiées à chaque trimestre.

ATTENTION : les seuils viennent de sources secondaires (le texte officiel
n'ayant pas pu être consulté) ; les sources divergent notamment sur le
nombre de spécialistes exigés. Ils sont tous paramétrables.
"""

from dataclasses import dataclass, field

from . import ccn
from .models import Clinic, EquipmentType, Specialty, StaffMember

Format = Clinic.Format

CHV_IMAGING_EQUIPMENT = ("radio-numerique", "echographe", "scanner", "irm")
CHV_LAB_EQUIPMENT = ("lab-biochimie", "lab-hemato", "lab-ionogramme")
CHV_FITOUT_CODE = "amenagement-chv"

DESCRIPTIONS = {
    Format.STANDARD: "Offre complète selon votre matériel et vos spécialistes, sans contrainte particulière.",
    Format.LOW_COST: "Actes de base uniquement (consultation, vaccination, stérilisation, détartrage). "
    "Protocoles standardisés : temps par acte réduit, consommables moins chers.",
    Format.CHV: "Urgences et hospitalisation 24 h/24, cas référés pris en charge par au moins 6 spécialistes "
    "(échelon 5, activité limitée à leur discipline). Plus attractif, mais exige un plateau technique complet "
    "et une permanence jour et nuit coûteuse.",
}


@dataclass
class Capabilities:
    """Ce dont dispose une clinique pour réaliser des actes : matériel et spécialistes."""

    equipment_ids: set = field(default_factory=set)
    specialties: set = field(default_factory=set)


def clinic_capabilities(clinic, quarter: int) -> Capabilities:
    from .services import active_equipment, active_staff

    return Capabilities(
        equipment_ids=set(active_equipment(clinic, quarter).values_list("equipment_type_id", flat=True)),
        specialties=set(active_staff(clinic, quarter).exclude(specialty="").values_list("specialty", flat=True)),
    )


def unavailability_reason(clinic_format: str, service_type, caps: Capabilities) -> str | None:
    """Pourquoi une prestation ne peut pas être proposée (``None`` si elle le peut)."""
    if service_type.required_format and service_type.required_format != clinic_format:
        return f"réservée au format {Format(service_type.required_format).label}"
    if clinic_format == Format.LOW_COST and (service_type.required_equipment_id or service_type.required_specialty):
        return "hors offre low-cost"
    eq = service_type.required_equipment_id
    if eq and eq not in caps.equipment_ids:
        return f"nécessite : {service_type.required_equipment.name}"
    if service_type.required_specialty and service_type.required_specialty not in caps.specialties:
        return f"nécessite un spécialiste en {Specialty(service_type.required_specialty).label.lower()}"
    return None


def chv_requirements(vets: int, asvs: int, specialties: list[str], owned_codes: set, params) -> list[tuple[str, bool]]:
    """Liste (libellé, condition remplie) des exigences du CHV.

    ``asvs`` : personnes qualifiées, c'est-à-dire ASV titrées (échelon 5).
    ``specialties`` : une entrée par vétérinaire spécialiste à l'échelon 5 (doublons possibles).
    """
    names = dict(
        EquipmentType.objects.filter(code__in=CHV_IMAGING_EQUIPMENT + CHV_LAB_EQUIPMENT).values_list("code", "name")
    )
    min_vets, min_asv = int(params.chv_min_vets), int(params.chv_min_asv)
    min_spe, min_disc, min_img = int(params.chv_min_specialists), int(params.chv_min_specialties), int(params.chv_min_imaging)
    imaging = [names.get(c, c) for c in CHV_IMAGING_EQUIPMENT if c in owned_codes]
    disciplines = sorted({Specialty(s).label.lower() for s in specialties})
    rows = [
        (f"Au moins {min_vets} vétérinaires à temps plein (actuellement {vets})", vets >= min_vets),
        (f"Au moins {min_asv} ASV titrées (échelon 5) à temps plein (actuellement {asvs})", asvs >= min_asv),
        (
            f"Au moins {min_spe} vétérinaires spécialistes (échelon 5) dans {min_disc} disciplines "
            f"(actuellement {len(specialties)} : {', '.join(disciplines) or 'aucun'})",
            len(specialties) >= min_spe and len(disciplines) >= min_disc,
        ),
        (
            f"Au moins {min_img} techniques d'imagerie (actuellement {len(imaging)} : {', '.join(imaging) or 'aucune'})",
            len(imaging) >= min_img,
        ),
    ]
    rows += [(names.get(code, code), code in owned_codes) for code in CHV_LAB_EQUIPMENT]
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
    vets = staff.filter(role=StaffMember.Role.VET)
    return chv_requirements(
        vets.count(),
        staff.filter(role=StaffMember.Role.ASV, level=ccn.QUALIFIED_ASV_LEVEL).count(),
        list(vets.exclude(specialty="").filter(level=ccn.SPECIALIST_LEVEL).values_list("specialty", flat=True)),
        owned,
        params,
    )


def missing_chv_requirements(clinic, quarter: int, params, durable: bool = False) -> list[str]:
    return [label for label, ok in current_chv_requirements(clinic, quarter, params, durable) if not ok]


def has_chv_fitout(clinic) -> bool:
    return clinic.equipment.filter(equipment_type__code=CHV_FITOUT_CODE).exists()


def sync_services(clinic, quarter: int):
    """Désactive les prestations devenues impossibles et active celles réservées au format."""
    caps = clinic_capabilities(clinic, quarter)
    for cs in clinic.services.select_related("service_type__required_equipment"):
        reason = unavailability_reason(clinic.format, cs.service_type, caps)
        if reason and cs.active:
            cs.active = False
            cs.save(update_fields=["active"])
        elif not reason and cs.service_type.required_format and not cs.active:
            cs.active = True  # les actes de CHV sont proposés dès l'obtention du statut
            cs.save(update_fields=["active"])


def activate_enabled_services(clinic, quarter: int, **service_filter):
    """Active les prestations (filtrées) que la clinique vient de rendre possibles."""
    caps = clinic_capabilities(clinic, quarter)
    for cs in clinic.services.select_related("service_type__required_equipment").filter(active=False, **service_filter):
        if unavailability_reason(clinic.format, cs.service_type, caps) is None:
            cs.active = True
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
