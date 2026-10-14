from django.core.management.base import BaseCommand, CommandError

from engine.resolution import resolve_due_servers, resolve_quarter
from game.models import GameServer


class Command(BaseCommand):
    help = (
        "Résout le trimestre en cours. Sans option : tous les serveurs dont l'échéance est "
        "passée (à lancer par cron, p. ex. chaque dimanche soir)."
    )

    def add_arguments(self, parser):
        parser.add_argument("--server", type=int, help="Forcer la résolution d'un serveur précis.")
        parser.add_argument("--quarters", type=int, default=1, help="Nombre de trimestres à enchaîner (avec --server).")

    def handle(self, *args, server=None, quarters=1, **options):
        if server is None:
            done = resolve_due_servers()
            self.stdout.write(self.style.SUCCESS(f"{len(done)} serveur(s) résolu(s)."))
            return
        try:
            game = GameServer.objects.get(pk=server)
        except GameServer.DoesNotExist:
            raise CommandError(f"Serveur {server} introuvable.")
        for _ in range(quarters):
            game.refresh_from_db()
            if game.status != GameServer.Status.RUNNING:
                break
            quarter = game.current_quarter
            reports = resolve_quarter(game)
            self.stdout.write(self.style.MIGRATE_HEADING(f"\n{game} – trimestre {quarter}"))
            self.stdout.write(f"{'Clinique':<34}{'CA HT':>12}{'Résultat':>12}{'Tréso':>12}{'PdM':>7}{'Capa':>6}")
            for r in sorted(reports, key=lambda r: -r.net_result):
                self.stdout.write(
                    f"{r.clinic.name:<34}{r.revenue:>12,.0f}{r.net_result:>12,.0f}"
                    f"{r.cash_end:>12,.0f}{r.market_share:>7.1%}{r.capacity_ratio:>6.0%}"
                )
