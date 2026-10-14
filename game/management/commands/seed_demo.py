"""Crée une partie de démonstration avec des cliniques « bots » aux stratégies variées.

Utile pour tester le moteur et pour les stress tests :

    python manage.py seed_demo --clinics 10 --seed 7 --param demand_multiplier=0.8
    python manage.py resolve_quarters --server <id> --quarters 4
"""

import random
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.management.base import BaseCommand, CommandError
from django.db import transaction

from accounting.loans import borrow
from clinics.services import acquire_equipment, create_clinic, hire
from decisions.models import PendingDecision
from game.catalog import seed_catalog
from game.map import generate_zones
from game.models import GameServer

CHV_EQUIPMENT = [
    ("radio-numerique", "purchase"),
    ("echographe", "purchase"),
    ("scanner", "lease"),
    ("lab-biochimie", "lease"),
    ("lab-hemato", "lease"),
    ("lab-ionogramme", "lease"),
    ("bloc-orthopedie", "lease"),
    ("endoscope", "lease"),
]

CHV_SPECIALISTS = ["chirurgie", "chirurgie", "medecine_interne", "medecine_interne", "imagerie", "imagerie"]

STRATEGIES = [
    # nom, format, multiplicateur de prix, matériel, véto généralistes en plus (échelon), spécialistes,
    # ASV en plus, réassort (en % de la demande estimée), emprunt (montant, trimestres) ou None
    ("Low-cost", "low_cost", 0.80, [], (1, 2), [], 0, 0.8, None),
    ("Premium équipée", "standard", 1.25, [("lab-biochimie", "purchase"), ("radio-numerique", "lease")], (1, 4), [], 1, 1.2, None),
    ("Équilibrée", "standard", 1.00, [("lab-biochimie", "lease")], (1, 3), [], 1, 1.0, None),
    ("Prudente", "standard", 1.05, [], (0, 3), [], 0, 0.6, None),
    ("CHV", "chv", 1.15, CHV_EQUIPMENT, (7, 3), CHV_SPECIALISTS, 7, 1.0, (200000, 28)),
    ("Référé chirurgie", "standard", 1.10, [("radio-numerique", "lease"), ("bloc-orthopedie", "lease")], (1, 3), ["chirurgie"], 1, 1.0, None),
]

# Salaires des généralistes par échelon (minimums 2026 : 2 729,92 / 3 268,72 / 3 771,60 €).
VET_SALARIES = {2: Decimal("2800"), 3: Decimal("3800"), 4: Decimal("4300")}
SPECIALIST_SALARY = Decimal("6500")  # échelon 5 (minimum 4 310,40 €), au prix du marché
ASV_SALARY = Decimal("2200")  # échelon 5, minimum 2026 : 2 155,20 €


class Command(BaseCommand):
    help = "Crée un serveur de démonstration peuplé de cliniques gérées par des bots."

    def add_arguments(self, parser):
        parser.add_argument("--name", default="Partie de démonstration")
        parser.add_argument("--clinics", type=int, default=10)
        parser.add_argument("--seed", type=int, default=42)
        parser.add_argument("--quarters", type=int, default=4)
        parser.add_argument("--free-slots", type=int, default=2, help="Places laissées libres pour des étudiants.")
        parser.add_argument("--join-code", default="", help="Code d'accès à communiquer aux étudiants.")
        parser.add_argument(
            "--param", action="append", default=[], help="Surcharge de paramètre, ex. demand_multiplier=0.8"
        )

    @transaction.atomic
    def handle(self, *args, name, clinics, seed, quarters, param, free_slots, join_code, **options):
        overrides = {}
        for item in param:
            key, _, value = item.partition("=")
            try:
                overrides[key] = float(value)
            except ValueError:
                raise CommandError(f"Paramètre invalide : {item}")

        seed_catalog()
        server = GameServer.objects.create(
            name=name,
            seed=seed,
            total_quarters=quarters,
            parameters=overrides,
            max_clinics=clinics + free_slots,
            join_code=join_code,
        )
        generate_zones(server)
        rng = random.Random(seed)
        cells = rng.sample([(x, y) for x in range(server.grid_width) for y in range(server.grid_height)], clinics)
        User = get_user_model()

        for i, (x, y) in enumerate(cells, start=1):
            strategy, clinic_format, price_mult, equipment, (extra_vets, vet_level), specialists, extra_asv, stock_factor, loan = (
                STRATEGIES[(i - 1) % len(STRATEGIES)]
            )
            owner, _ = User.objects.get_or_create(username=f"bot{server.pk}_{i}")
            clinic = create_clinic(server, owner, f"Clinique {i} ({strategy})", x, y)
            if loan:
                # L'emprunt est débloqué avant les achats pour financer le plateau technique.
                borrow(clinic, Decimal(loan[0]), loan[1], server.get_parameters())
            for code, mode in equipment:
                acquire_equipment(clinic, code, mode)
            for _ in range(extra_vets):
                hire(clinic, "vet", "Vétérinaire salarié", VET_SALARIES[vet_level], level=vet_level)
            for specialty in specialists:
                hire(clinic, "vet", "Vétérinaire spécialiste", SPECIALIST_SALARY, level=5, specialty=specialty)
            for _ in range(extra_asv):
                hire(clinic, "asv", "ASV", ASV_SALARY)
            if clinic_format != "standard":
                PendingDecision.objects.create(
                    clinic=clinic,
                    quarter=server.current_quarter,
                    kind=PendingDecision.Kind.SET_FORMAT,
                    payload={"format": clinic_format},
                )
            # Les prix et le stock passent par des décisions : on exerce le même chemin qu'un joueur.
            for cs in clinic.services.select_related("service_type"):
                PendingDecision.objects.create(
                    clinic=clinic,
                    quarter=server.current_quarter,
                    kind=PendingDecision.Kind.SET_SERVICE,
                    payload={"service": cs.service_type.code, "price": str(round(cs.service_type.reference_price * Decimal(str(price_mult)), 2))},
                )
            for item in clinic.stock.select_related("product_line"):
                target = round(1500 * item.product_line.units_per_animal * stock_factor)
                PendingDecision.objects.create(
                    clinic=clinic,
                    quarter=server.current_quarter,
                    kind=PendingDecision.Kind.SET_PRODUCT,
                    payload={"product": item.product_line.code, "target_quantity": target},
                )

        server.start()
        self.stdout.write(self.style.SUCCESS(f"Serveur #{server.pk} « {server} » créé avec {clinics} cliniques."))
