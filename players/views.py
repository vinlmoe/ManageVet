"""Inscription des étudiants et installation de leur clinique sur une partie."""

from django.conf import settings
from django.contrib import messages
from django.contrib.auth import login
from django.contrib.auth.decorators import login_required
from django.db import IntegrityError, transaction
from django.http import Http404
from django.shortcuts import get_object_or_404, redirect, render

from clinics.services import OperationError, create_clinic
from game.models import GameServer
from game.placement import CATCHMENT_KM, cell_insights

from .forms import JoinServerForm, SignupForm


def signup(request):
    if not settings.SIGNUP_ENABLED:
        raise Http404("Les inscriptions sont fermées.")
    if request.user.is_authenticated:
        return redirect("game:server_list")
    form = SignupForm(request.POST or None)
    if request.method == "POST" and form.is_valid():
        user = form.save()
        login(request, user, backend="django.contrib.auth.backends.ModelBackend")
        messages.success(request, "Compte créé. Choisissez une partie et installez votre clinique.")
        return redirect("game:server_list")
    return render(request, "registration/signup.html", {"form": form})


@login_required
def join_server(request, pk):
    server = get_object_or_404(GameServer, pk=pk)
    existing = server.clinics.filter(owner=request.user).first()
    if existing:
        messages.info(request, "Vous dirigez déjà une clinique sur cette partie.")
        return redirect("game:clinic_dashboard", pk=existing.pk)
    if not server.accepts_new_clinics():
        messages.error(request, "Cette partie n'accepte plus de nouvelles cliniques.")
        return redirect("game:server_list")

    form = JoinServerForm(request.POST or None, server=server)
    if request.method == "POST" and form.is_valid():
        x, y = form.cleaned_data["cell"]
        try:
            with transaction.atomic():
                # Verrou : deux étudiants ne peuvent pas prendre la dernière place
                # ou la même case simultanément.
                locked = GameServer.objects.select_for_update().get(pk=server.pk)
                if not locked.accepts_new_clinics():
                    raise OperationError("La partie vient d'être complétée.")
                clinic = create_clinic(locked, request.user, form.cleaned_data["name"], x, y)
        except (OperationError, IntegrityError) as exc:
            message = str(exc) if isinstance(exc, OperationError) else "Cette case vient d'être prise."
            form.add_error(None, message)
        else:
            messages.success(
                request,
                f"Bienvenue ! « {clinic.name} » est installée en ({x}, {y}). "
                "Fixez vos tarifs et votre stock avant la résolution du trimestre.",
            )
            return redirect("decisions:board", pk=clinic.pk)

    return render(request, "players/join.html", _join_context(server, form))


def _join_context(server, form):
    insights = cell_insights(server)
    clinics = {(c.x, c.y): c for c in server.clinics.all()}
    max_animals = max((i.animals for i in insights.values()), default=1) or 1
    selected = form.data.get("cell") if form.is_bound else None
    rows = [
        [
            {
                "insight": insights[(x, y)],
                "density": insights[(x, y)].animals / max_animals,
                "clinic": clinics.get((x, y)),
                "value": f"{x},{y}",
            }
            for x in range(server.grid_width)
        ]
        for y in range(server.grid_height)
    ]
    return {
        "server": server,
        "form": form,
        "rows": rows,
        "selected": selected,
        "catchment_km": CATCHMENT_KM,
        "free_slots": server.max_clinics - len(clinics),
    }
