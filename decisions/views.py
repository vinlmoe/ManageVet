"""Écran de saisie des décisions du joueur pour le trimestre en cours.

Chaque formulaire crée des ``PendingDecision`` : rien n'est appliqué avant la
résolution hebdomadaire. Un même objet (prestation, gamme) ne porte qu'une
décision en attente à la fois : une nouvelle saisie remplace la précédente.
"""

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from clinics.access import get_clinic_for_user
from clinics.models import EquipmentType
from clinics.services import active_equipment
from game.models import GameServer

from . import planning
from .forms import AcquireForm, HireForm, IdForm, ProductsForm, ServicesForm
from .models import PendingDecision

Kind = PendingDecision.Kind


def _services_form(clinic, quarter, data=None):
    owned = set(active_equipment(clinic, quarter).values_list("equipment_type_id", flat=True))
    clinic_services = list(clinic.services.select_related("service_type__required_equipment").order_by("service_type__id"))
    operable = {
        cs.service_type.code
        for cs in clinic_services
        if cs.service_type.required_equipment_id is None or cs.service_type.required_equipment_id in owned
    }
    form = ServicesForm(
        data, clinic_services=clinic_services, plan=planning.planned_services(clinic, quarter), operable_codes=operable
    )
    return form


def _products_form(clinic, quarter, data=None):
    items = list(clinic.stock.select_related("product_line").order_by("product_line__id"))
    return ProductsForm(data, stock_items=items, plan=planning.planned_products(clinic, quarter))


@login_required
def decision_board(request, pk):
    clinic = get_clinic_for_user(request.user, pk)
    server = clinic.server
    can_edit = server.status == GameServer.Status.RUNNING
    forms = {}

    if request.method == "POST":
        if not can_edit:
            messages.error(request, "La partie n'est pas en cours : aucune décision ne peut être saisie.")
            return redirect("decisions:board", pk=clinic.pk)
        action = request.POST.get("action")
        handler = ACTIONS.get(action)
        if handler is None:
            messages.error(request, "Action inconnue.")
            return redirect("decisions:board", pk=clinic.pk)
        with transaction.atomic():
            # Verrou sur le serveur : la résolution ne peut pas s'intercaler et
            # la décision vise bien le trimestre en cours.
            server = GameServer.objects.select_for_update().get(pk=server.pk)
            clinic.server = server
            if server.status != GameServer.Status.RUNNING:
                messages.error(request, "Le trimestre vient d'être résolu ; rechargez la page.")
                return redirect("decisions:board", pk=clinic.pk)
            invalid_form = handler(request, clinic, server.current_quarter)
        if invalid_form is None:
            return redirect("decisions:board", pk=clinic.pk)
        forms[action] = invalid_form  # réaffichage avec les erreurs

    quarter = server.current_quarter
    params = server.get_parameters()
    pending = list(planning.pending(clinic, quarter).order_by("created_at", "id"))
    pending_rows = [(d, *planning.describe(d, params)) for d in pending]
    pending_dismiss = {d.payload.get("staff_id") for d in pending if d.kind == Kind.DISMISS}
    pending_acquire = {d.payload.get("equipment") for d in pending if d.kind == Kind.ACQUIRE_EQUIPMENT}
    pending_release = {d.payload.get("clinic_equipment_id") for d in pending if d.kind == Kind.RELEASE_EQUIPMENT}
    owned = list(active_equipment(clinic, quarter).select_related("equipment_type"))
    owned_codes = {e.equipment_type.code for e in owned}

    context = {
        "clinic": clinic,
        "server": server,
        "quarter": quarter,
        "can_edit": can_edit,
        "params": params,
        "services_form": forms.get("services") or _services_form(clinic, quarter),
        "products_form": forms.get("products") or _products_form(clinic, quarter),
        "hire_form": forms.get("hire") or HireForm(),
        "staff": [
            (m, m.pk in pending_dismiss) for m in clinic.staff.filter(left_quarter__isnull=True).order_by("role", "id")
        ],
        "owned_equipment": [(e, e.pk in pending_release) for e in owned],
        "catalog": [
            (eq, eq.code in pending_acquire)
            for eq in EquipmentType.objects.order_by("purchase_price")
            if eq.code not in owned_codes
        ],
        "pending_rows": pending_rows,
        "pending_cash_impact": sum((impact for _, _, impact in pending_rows if impact is not None), 0),
        "history": clinic.decisions.exclude(status=PendingDecision.Status.PENDING).order_by("-quarter", "-id")[:15],
    }
    return render(request, "decisions/board.html", context)


# --- Actions ------------------------------------------------------------------
# Chaque action renvoie None en cas de succès, ou le formulaire invalide à réafficher.


def _save_services(request, clinic, quarter):
    form = _services_form(clinic, quarter, request.POST)
    if not form.is_valid():
        return form
    actual = {cs.service_type.code: {"price": cs.price, "active": cs.active} for cs, _ in form.rows}
    plan = planning.planned_services(clinic, quarter)
    changed = 0
    for cs, code in form.rows:
        new = {"price": form.cleaned_data[f"price_{code}"], "active": plan[code]["active"]}
        if not form.fields[f"active_{code}"].disabled:
            new["active"] = form.cleaned_data[f"active_{code}"]
        if new == plan[code]:
            continue
        changed += 1
        payload = None if new == actual[code] else {"service": code, "price": str(new["price"]), "active": new["active"]}
        planning.replace_pending(clinic, quarter, Kind.SET_SERVICE, "service", code, payload)
    _report(request, changed, "prestation")
    return None


def _save_products(request, clinic, quarter):
    form = _products_form(clinic, quarter, request.POST)
    if not form.is_valid():
        return form
    actual = {i.product_line.code: {"retail_price": i.retail_price, "target_quantity": i.target_quantity} for i, _ in form.rows}
    plan = planning.planned_products(clinic, quarter)
    changed = 0
    for _, code in form.rows:
        new = {"retail_price": form.cleaned_data[f"price_{code}"], "target_quantity": form.cleaned_data[f"target_{code}"]}
        if new == plan[code]:
            continue
        changed += 1
        payload = None if new == actual[code] else {
            "product": code, "retail_price": str(new["retail_price"]), "target_quantity": new["target_quantity"]
        }
        planning.replace_pending(clinic, quarter, Kind.SET_PRODUCT, "product", code, payload)
    _report(request, changed, "gamme")
    return None


def _report(request, changed, noun):
    if changed:
        messages.success(request, f"{changed} {noun}{'s' if changed > 1 else ''} modifiée{'s' if changed > 1 else ''}.")
    else:
        messages.info(request, "Aucune modification.")


def _hire(request, clinic, quarter):
    form = HireForm(request.POST)
    if not form.is_valid():
        return form
    data = form.cleaned_data
    PendingDecision.objects.create(
        clinic=clinic,
        quarter=quarter,
        kind=Kind.HIRE,
        payload={"role": data["role"], "name": data["name"], "gross_monthly_salary": str(data["gross_monthly_salary"])},
    )
    messages.success(request, f"Recrutement de {data['name']} programmé.")
    return None


def _object_id(request):
    form = IdForm(request.POST)
    return form.cleaned_data["object_id"] if form.is_valid() else None


def _dismiss(request, clinic, quarter):
    staff_id = _object_id(request)
    member = clinic.staff.filter(pk=staff_id, left_quarter__isnull=True).first() if staff_id else None
    if member is None:
        messages.error(request, "Salarié introuvable.")
    elif planning.pending(clinic, quarter).filter(kind=Kind.DISMISS, payload__staff_id=member.pk).exists():
        messages.info(request, f"Le départ de {member.name} est déjà programmé.")
    else:
        PendingDecision.objects.create(clinic=clinic, quarter=quarter, kind=Kind.DISMISS, payload={"staff_id": member.pk})
        messages.success(request, f"Départ de {member.name} programmé.")
    return None


def _acquire(request, clinic, quarter):
    form = AcquireForm(request.POST)
    eq = EquipmentType.objects.filter(code=form.cleaned_data["equipment"]).first() if form.is_valid() else None
    if eq is None:
        messages.error(request, "Matériel inconnu.")
    elif active_equipment(clinic, quarter).filter(equipment_type=eq).exists():
        messages.error(request, f"La clinique dispose déjà de : {eq.name}.")
    else:
        # Une seule acquisition en attente par matériel : changer de mode remplace la précédente.
        planning.replace_pending(
            clinic, quarter, Kind.ACQUIRE_EQUIPMENT, "equipment", eq.code,
            {"equipment": eq.code, "mode": form.cleaned_data["mode"]},
        )
        mode = "Achat" if form.cleaned_data["mode"] == "purchase" else "Leasing"
        messages.success(request, f"{mode} programmé : {eq.name}.")
    return None


def _release(request, clinic, quarter):
    item_id = _object_id(request)
    item = active_equipment(clinic, quarter).filter(pk=item_id, mode="lease").first() if item_id else None
    if item is None:
        messages.error(request, "Contrat de leasing introuvable.")
    else:
        planning.replace_pending(
            clinic, quarter, Kind.RELEASE_EQUIPMENT, "clinic_equipment_id", item.pk, {"clinic_equipment_id": item.pk}
        )
        messages.success(request, f"Résiliation programmée : {item.equipment_type.name}.")
    return None


def _cancel(request, clinic, quarter):
    decision_id = _object_id(request)
    decision = planning.pending(clinic, quarter).filter(pk=decision_id).first() if decision_id else None
    if decision is None:
        messages.error(request, "Décision introuvable ou déjà traitée.")
    else:
        decision.status = PendingDecision.Status.CANCELLED
        decision.message = "Annulée par le joueur."
        decision.save(update_fields=["status", "message"])
        messages.success(request, "Décision annulée.")
    return None


ACTIONS = {
    "services": _save_services,
    "products": _save_products,
    "hire": _hire,
    "dismiss": _dismiss,
    "acquire": _acquire,
    "release": _release,
    "cancel": _cancel,
}
