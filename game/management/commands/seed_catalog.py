from django.core.management.base import BaseCommand

from game.catalog import seed_catalog


class Command(BaseCommand):
    help = "Crée ou met à jour le catalogue de référence (prestations, matériel, produits)."

    def handle(self, *args, **options):
        seed_catalog()
        self.stdout.write(self.style.SUCCESS("Catalogue de référence à jour."))
