"""Catalogue de référence (prestations, matériel, gammes de produits).

Valeurs indicatives, à affiner avec l'équipe pédagogique. La commande
``seed_catalog`` est idempotente : relancer met à jour les valeurs.
"""

from decimal import Decimal as D

from clinics.models import EquipmentType, ServiceType
from inventory.models import ProductLine

EQUIPMENT = [
    # code, nom, prix d'achat, leasing / trimestre, durée (trimestres)
    ("lab-biochimie", "Analyseur de biochimie", D("18000"), D("1300"), 20),
    ("lab-hemato", "Analyseur d'hématologie", D("12000"), D("900"), 20),
    ("radio-numerique", "Radiographie numérique", D("45000"), D("3200"), 28),
    ("echographe", "Échographe", D("30000"), D("2100"), 20),
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
    for code, name, price, freq, vet, asv, cons, eq in SERVICES:
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
