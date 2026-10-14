"""Vues en lecture seule : carte du serveur et tableau de bord d'une clinique.

La saisie des décisions passe pour l'instant par l'admin ; une interface
joueur dédiée viendra dans une itération suivante.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from accounting.services import account_balance
from accounting import pcg
from clinics.models import Clinic

from .models import GameServer


@login_required
def server_list(request):
    return render(request, "game/server_list.html", {"servers": GameServer.objects.order_by("-created_at")})


@login_required
def server_map(request, pk):
    server = get_object_or_404(GameServer, pk=pk)
    zones = {(z.x, z.y): z for z in server.zones.all()}
    clinics = {(c.x, c.y): c for c in server.clinics.all()}
    max_animals = max((z.dogs + z.cats for z in zones.values()), default=1) or 1
    rows = []
    for y in range(server.grid_height):
        row = []
        for x in range(server.grid_width):
            zone = zones.get((x, y))
            animals = (zone.dogs + zone.cats) if zone else 0
            row.append({"x": x, "y": y, "animals": animals, "density": animals / max_animals, "clinic": clinics.get((x, y))})
        rows.append(row)
    last_quarter = server.current_quarter - (0 if server.status == GameServer.Status.FINISHED else 1)
    ranking = (
        server.clinics.filter(reports__quarter=last_quarter)
        .values("pk", "name", "reports__net_result", "reports__market_share", "cash")
        .order_by("-reports__net_result")
    )
    return render(
        request, "game/server_map.html", {"server": server, "rows": rows, "ranking": ranking, "last_quarter": last_quarter}
    )


@login_required
def clinic_dashboard(request, pk):
    clinic = get_object_or_404(Clinic.objects.select_related("server"), pk=pk)
    entries = clinic.journal_entries.prefetch_related("lines").order_by("-quarter", "-id")[:60]
    return render(
        request,
        "game/clinic_dashboard.html",
        {
            "clinic": clinic,
            "reports": clinic.reports.order_by("quarter"),
            "staff": clinic.staff.filter(left_quarter__isnull=True),
            "equipment": clinic.equipment.select_related("equipment_type").filter(released_quarter__isnull=True),
            "stock": clinic.stock.select_related("product_line"),
            "decisions": clinic.decisions.order_by("-created_at")[:20],
            "entries": entries,
            "bank_balance": account_balance(clinic, pcg.BANK),
        },
    )
