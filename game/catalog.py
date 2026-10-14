"""Catalogue de référence (prestations, matériel, gammes de produits).

Valeurs indicatives, à affiner avec l'équipe pédagogique. La commande
``seed_catalog`` est idempotente : relancer met à jour les valeurs.
"""

from decimal import Decimal as D

from clinics.models import Clinic, ClinicService, EquipmentType, ServiceType
from inventory.models import ProductLine, StockItem

EQUIPMENT = [
    # code, nom, prix d'achat, leasing / trimestre, durée (trimestres)
    ("lab-biochimie", "Analyseur de biochimie", D("18000"), D("1300"), 20),
    ("lab-hemato", "Analyseur d'hématologie", D("12000"), D("900"), 20),
    ("radio-numerique", "Radiographie numérique", D("45000"), D("3200"), 28),
    ("echographe", "Échographe", D("30000"), D("2100"), 20),
    ("lab-ionogramme", "Analyseur d'ionogrammes", D("8000"), D("600"), 20),
    ("scanner", "Scanner (tomodensitométrie)", D("180000"), D("11000"), 28),
    ("bloc-orthopedie", "Bloc de chirurgie spécialisée (instrumentation orthopédique, amplificateur de brillance)", D("90000"), D("5500"), 28),
    ("endoscope", "Colonne d'endoscopie", D("45000"), D("2800"), 20),
]

# Immobilisations hors catalogue : code, nom, coût, compte, durée (trimestres)
FIXED_ASSETS = [
    (
        "amenagement-chv",
        "Aménagements CHV (salle d'urgence, chenil d'hospitalisation, bloc)",
        D("60000"),
        "2135",
        40,
    ),
]

SERVICES = [
    # code, nom, prix réf., fréquence/animal/trim., min véto, min ASV, consommables, matériel requis
    ("consultation", "Consultation", D("45"), 0.45, 20, 10, D("2"), None),
    ("vaccination", "Vaccination", D("60"), 0.25, 15, 5, D("12"), None),
    ("sterilisation", "Stérilisation / chirurgie courante", D("250"), 0.03, 60, 60, D("35"), None),
    ("detartrage", "Détartrage", D("150"), 0.03, 45, 45, D("15"), None),
    ("biochimie", "Bilan biochimique", D("70"), 0.08, 5, 15, D("15"), "lab-biochimie"),
    ("hematologie", "Numération formule sanguine", D("45"), 0.06, 5, 10, D("8"), "lab-hemato"),
    ("radiographie", "Radiographie", D("90"), 0.05, 15, 15, D("5"), "radio-numerique"),
    ("echographie", "Échographie", D("100"), 0.03, 30, 5, D("3"), "echographe"),
    ("ionogramme", "Ionogramme", D("40"), 0.02, 5, 10, D("6"), "lab-ionogramme"),
    ("scanner", "Scanner", D("450"), 0.003, 30, 45, D("40"), "scanner"),
]

# Actes d'équipement lourd sur lesquels les confrères réfèrent leurs patients.
REFERRAL_EQUIPMENT_SERVICES = {"scanner"}

# Actes de spécialistes : cas référés que les autres cliniques ne peuvent pas prendre en charge.
# Accessibles à toute clinique qui emploie le spécialiste (sauf low-cost) ; un spécialiste
# ne réalise que les actes de sa discipline.
# code, nom, prix réf., fréquence, min véto, min ASV, consommables, spécialité, matériel requis
SPECIALIST_SERVICES = [
    ("consultation-medecine-interne", "Consultation référée de médecine interne", D("110"), 0.016, 45, 10, D("5"), "medecine_interne", None),
    ("bilan-medecine-interne", "Bilan de médecine interne (endoscopie, explorations)", D("380"), 0.011, 75, 45, D("90"), "medecine_interne", "endoscope"),
    ("consultation-chirurgicale", "Consultation chirurgicale et orthopédique référée", D("90"), 0.016, 30, 10, D("2"), "chirurgie", None),
    ("chirurgie-specialisee", "Chirurgie spécialisée (orthopédie, tissus mous)", D("1200"), 0.0055, 120, 120, D("380"), "chirurgie", "bloc-orthopedie"),
    ("imagerie-specialisee", "Interprétation d'imagerie en coupe (scanner)", D("150"), 0.016, 30, 5, D("0"), "imagerie", "scanner"),
    ("echographie-specialisee", "Échographie spécialisée (abdominale, cardiaque)", D("180"), 0.016, 40, 10, D("5"), "imagerie", "echographe"),
]

# Prestations réservées aux CHV (permanence 24 h/24).
# code, nom, prix réf., fréquence, min véto, min ASV, consommables
CHV_SERVICES = [
    ("urgences", "Urgences (nuit, dimanche, jours fériés)", D("140"), 0.03, 40, 30, D("15")),
    ("hospitalisation", "Hospitalisation continue (journée)", D("95"), 0.035, 15, 60, D("20")),
]

PRODUCTS = [
    # code, nom, espèce, coût unitaire, prix conseillé, unités/animal/trim.
    ("croq-chien-premium", "Croquettes chien premium (12 kg)", "dog", D("38"), D("65"), 0.15),
    ("croq-chat-premium", "Croquettes chat premium (3 kg)", "cat", D("18"), D("32"), 0.25),
    ("diet-chien", "Aliment diététique chien", "dog", D("45"), D("78"), 0.05),
    ("diet-chat", "Aliment diététique chat", "cat", D("22"), D("39"), 0.06),
    ("antiparasitaires", "Antiparasitaires (boîte)", "all", D("14"), D("28"), 0.30),
]


def seed_catalog():
    equipment = {}
    for code, name, price, lease, life in EQUIPMENT:
        equipment[code], _ = EquipmentType.objects.update_or_create(
            code=code,
            defaults={"name": name, "purchase_price": price, "lease_per_quarter": lease, "useful_life_quarters": life},
        )
    for code, name, price, account, life in FIXED_ASSETS:
        EquipmentType.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "purchase_price": price,
                "lease_per_quarter": D("0"),
                "useful_life_quarters": life,
                "asset_account": account,
                "purchasable": False,
            },
        )
    rows = (
        [(*row, "", "") for row in SERVICES]
        + [(*row[:7], row[8], "", row[7]) for row in SPECIALIST_SERVICES]
        + [(*row, None, "chv", "") for row in CHV_SERVICES]
    )
    referral = {code for code, *_ in SPECIALIST_SERVICES + CHV_SERVICES} | REFERRAL_EQUIPMENT_SERVICES
    for code, name, price, freq, vet, asv, cons, eq, required_format, specialty in rows:
        ServiceType.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "reference_price": price,
                "frequency_per_animal": freq,
                "vet_minutes": vet,
                "asv_minutes": asv,
                "consumables_cost": cons,
                "required_equipment": equipment.get(eq),
                "required_format": required_format,
                "required_specialty": specialty,
                "referral": code in referral,
            },
        )
    for code, name, species, cost, price, units in PRODUCTS:
        ProductLine.objects.update_or_create(
            code=code,
            defaults={
                "name": name,
                "species": species,
                "unit_cost": cost,
                "recommended_price": price,
                "units_per_animal": units,
            },
        )

    ensure_clinic_offers()


def ensure_clinic_offers():
    """Ajoute aux cliniques existantes les prestations et gammes apparues dans le catalogue.

    Nouvelles lignes inactives (prestations) ou sans réassort (gammes), au prix de référence.
    """
    for clinic in Clinic.objects.all():
        for st in ServiceType.objects.exclude(pk__in=clinic.services.values("service_type")):
            ClinicService.objects.create(clinic=clinic, service_type=st, price=st.reference_price, active=False)
        for pl in ProductLine.objects.exclude(pk__in=clinic.stock.values("product_line")):
            StockItem.objects.create(clinic=clinic, product_line=pl, retail_price=pl.recommended_price)
