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

from clinics.services import acquire_equipment, create_clinic, hire
from decisions.models import PendingDecision
from game.catalog import seed_catalog
from game.map import generate_zones
from game.models import GameServer

STRATEGIES = [
    # nom, multiplicateur de prix, matériel, véto en plus, ASV en plus, réassort (en % de la demande estimée)
    ("Low-cost", 0.80, [], 1, 0, 0.8),
    ("Premium équipée", 1.25, [("lab-biochimie", "purchase"), ("radio-numerique", "lease")], 1, 1, 1.2),
    ("Équilibrée", 1.00, [("lab-biochimie", "lease")], 1, 1, 1.0),
    ("Prudente", 1.05, [], 0, 0, 0.6),
    ("Plateau technique", 1.10, [("lab-hemato", "lease"), ("echographe", "lease"), ("lab-biochimie", "lease")], 2, 1, 1.0),
]


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
            strategy, price_mult, equipment, extra_vets, extra_asv, stock_factor = STRATEGIES[(i - 1) % len(STRATEGIES)]
            owner, _ = User.objects.get_or_create(username=f"bot{server.pk}_{i}")
            clinic = create_clinic(server, owner, f"Clinique {i} ({strategy})", x, y)
            for code, mode in equipment:
                acquire_equipment(clinic, code, mode)
            for _ in range(extra_vets):
                hire(clinic, "vet", "Vétérinaire salarié", Decimal("3800"))
            for _ in range(extra_asv):
                hire(clinic, "asv", "ASV", Decimal("1900"))
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
