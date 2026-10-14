"""Vues en lecture seule : liste des parties, carte et tableau de bord d'une clinique.

La saisie des décisions est dans l'application ``decisions``.
"""

from django.contrib.auth.decorators import login_required
from django.shortcuts import get_object_or_404, render

from accounting.services import account_balance
from accounting import pcg
from clinics.access import get_clinic_for_user

from .models import GameServer


@login_required
def server_list(request):
    my_clinics = {c.server_id: c for c in request.user.clinics.all()}
    servers = [(s, my_clinics.get(s.pk)) for s in GameServer.objects.order_by("-created_at")]
    return render(request, "game/server_list.html", {"servers": servers})


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
        request,
        "game/server_map.html",
        {
            "server": server,
            "rows": rows,
            "ranking": ranking,
            "last_quarter": last_quarter,
            # Liens vers les tableaux de bord : sa propre clinique, ou toutes pour l'équipe pédagogique.
            "visible_clinics": set(server.clinics.values_list("pk", flat=True))
            if request.user.is_staff
            else set(server.clinics.filter(owner=request.user).values_list("pk", flat=True)),
        },
    )


@login_required
def clinic_dashboard(request, pk):
    clinic = get_clinic_for_user(request.user, pk)
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
            "loans": [
                (loan, loan.annual_rate * 100) for loan in clinic.loans.filter(status="active")
            ],
        },
    )
