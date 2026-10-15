"""Écran de saisie des décisions du joueur pour le trimestre en cours.

Chaque formulaire crée des ``PendingDecision`` : rien n'est appliqué avant la
résolution hebdomadaire. Un même objet (prestation, gamme) ne porte qu'une
décision en attente à la fois : une nouvelle saisie remplace la précédente.
"""

from decimal import Decimal

from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.db import transaction
from django.shortcuts import redirect, render

from accounting import loans
from accounting.models import Loan
from accounting.services import equity, outstanding_debt
from clinics import ccn, formats, recruitment
from clinics.access import get_clinic_for_user
from clinics.models import Clinic, EquipmentType
from clinics.services import active_equipment
from engine.resolution import permanence_fte
from game.models import GameServer

from . import planning
from .forms import AcquireForm, BorrowForm, FormatForm, HireForm, IdForm, ProductsForm, ServicesForm
from .models import PendingDecision

Kind = PendingDecision.Kind


def _services_form(clinic, quarter, data=None):
    caps = formats.clinic_capabilities(clinic, quarter)
    clinic_services = list(clinic.services.select_related("service_type__required_equipment").order_by("service_type__id"))
    for cs in clinic_services:
        cs.unavailable = formats.unavailability_reason(clinic.format, cs.service_type, caps)
    return ServicesForm(
        data,
        clinic_services=clinic_services,
        plan=planning.planned_services(clinic, quarter),
        operable_codes={cs.service_type.code for cs in clinic_services if cs.unavailable is None},
    )


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
    owned = list(active_equipment(clinic, quarter).select_related("equipment_type").filter(equipment_type__purchasable=True))
    owned_codes = {e.equipment_type.code for e in owned}
    target_format = planning.planned_format(clinic, quarter)
    pending_borrowed = sum(
        (Decimal(str(d.payload.get("amount", 0))) for d in pending if d.kind == Kind.BORROW), Decimal("0")
    )
    pending_repay = {d.payload.get("loan_id") for d in pending if d.kind == Kind.REPAY_LOAN}
    clinic_equity = equity(clinic)
    debt = outstanding_debt(clinic)
    last_report = clinic.reports.filter(quarter=quarter - 1).first()
    fitout = EquipmentType.objects.filter(code=formats.CHV_FITOUT_CODE).first()

    context = {
        "clinic": clinic,
        "server": server,
        "quarter": quarter,
        "can_edit": can_edit,
        "params": params,
        "services_form": forms.get("services") or _services_form(clinic, quarter),
        "products_form": forms.get("products") or _products_form(clinic, quarter),
        "hire_form": forms.get("hire") or HireForm(point_value=params.ccn_point_value),
        "salary_minimums": {
            f"{role}-{level}": str(ccn.minimum_salary(role, level, params.ccn_point_value))
            for role, levels in ccn.LEVELS.items()
            for level in levels
        },
        "recruitment_data": _recruitment_data(clinic, params),
        "recruitment_enabled": bool(params.recruitment_enabled),
        "staff": [
            (m, m.pk in pending_dismiss, ccn.productivity(m.role, m.level, params))
            for m in clinic.staff.filter(left_quarter__isnull=True).order_by("role", "specialty", "id")
        ],
        "productivities": [
            (role_label, ccn.level_label(role, level), ccn.productivity(role, level, params))
            for role, role_label in (("vet", "Vétérinaire"), ("asv", "Auxiliaire"))
            for level in ccn.LEVELS[role]
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
        # Positionnement
        "format_form": forms.get("format") or FormatForm(initial={"format": target_format}),
        "target_format": target_format,
        "format_options": [
            (value, label, formats.DESCRIPTIONS[value]) for value, label in Clinic.Format.choices
        ],
        "chv_requirements": planning.planned_chv_requirements(clinic, quarter, params),
        "chv_fitout_cost": None if formats.has_chv_fitout(clinic) or fitout is None else fitout.purchase_price,
        "chv_extra_costs": params.chv_extra_fixed_costs_per_quarter,
        "permanence_fte": permanence_fte(params),
        # Financement
        "borrow_form": forms.get("borrow") or BorrowForm(),
        "loans": [
            (loan, loan.pk in pending_repay, loans.early_repayment_penalty(loan, params), loan.annual_rate * 100)
            for loan in clinic.loans.filter(status=Loan.Status.ACTIVE)
        ],
        "rates": {
            "base": params.loan_base_rate_annual * 100,
            "premium": params.loan_risk_premium_per_leverage * 100,
            "loss": params.loan_loss_premium * 100,
        },
        "equity": clinic_equity,
        "debt": debt,
        "capacity": max(Decimal("0"), loans.borrowing_capacity(clinic, params) - pending_borrowed),
        "pending_borrowed": pending_borrowed,
        "last_loss": bool(last_report and last_report.net_result < 0),
        "durations": loans.DURATIONS,
    }
    return render(request, "decisions/board.html", context)


def _recruitment_data(clinic, params) -> dict:
    """Éléments du calcul des chances de recrutement, pour la simulation dans le navigateur."""
    factors = recruitment.clinic_factors(clinic, params)
    market = {}
    for role, levels in ccn.LEVELS.items():
        for level in levels:
            market[f"{role}-{level}"] = str(recruitment.market_salary(role, level, "", params))
    market["specialist-5"] = str(recruitment.market_salary("vet", 5, "chirurgie", params))
    return {
        "enabled": bool(params.recruitment_enabled),
        "market": market,
        "base": {kind: getattr(params, f"recruitment_base_{kind}") for kind in ("vet", "specialist", "asv")},
        "fee": {kind: getattr(params, f"recruitment_fee_{kind}") for kind in ("vet", "specialist", "asv")},
        "pool": {kind: getattr(params, f"candidates_{kind}_per_quarter") for kind in ("vet", "specialist", "asv")},
        "shortage": params.recruitment_shortage,
        "elasticity": params.recruitment_salary_elasticity,
        "cap": params.recruitment_salary_factor_cap,
        "location": factors["location"],
        "density": factors["density"],
        "reputation": factors["reputation"],
        "format": factors["format"],
    }


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
    form = HireForm(request.POST, point_value=clinic.server.get_parameters().ccn_point_value)
    if not form.is_valid():
        return form
    data = form.cleaned_data
    payload = {
        "role": data["role"],
        "level": data["level"],
        "name": data["name"],
        "gross_monthly_salary": str(data["gross_monthly_salary"]),
    }
    if data["specialty"]:
        payload["specialty"] = data["specialty"]
    PendingDecision.objects.create(clinic=clinic, quarter=quarter, kind=Kind.HIRE, payload=payload)
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


def _set_format(request, clinic, quarter):
    form = FormatForm(request.POST)
    if not form.is_valid():
        return form
    target = form.cleaned_data["format"]
    label = Clinic.Format(target).label
    if target == planning.planned_format(clinic, quarter):
        messages.info(request, f"Format inchangé : {label}.")
        return None
    # Une seule décision de format en attente : toute nouvelle saisie remplace la précédente.
    for d in planning.pending(clinic, quarter).filter(kind=Kind.SET_FORMAT):
        d.status = PendingDecision.Status.CANCELLED
        d.message = "Remplacée par une saisie plus récente."
        d.save(update_fields=["status", "message"])
    if target != clinic.format:
        PendingDecision.objects.create(clinic=clinic, quarter=quarter, kind=Kind.SET_FORMAT, payload={"format": target})
        messages.success(request, f"Passage au format « {label} » programmé (appliqué après vos autres décisions).")
    else:
        messages.success(request, "Changement de format annulé.")
    return None


def _borrow(request, clinic, quarter):
    form = BorrowForm(request.POST)
    if not form.is_valid():
        return form
    amount, duration = form.cleaned_data["amount"], form.cleaned_data["duration_quarters"]
    params = clinic.server.get_parameters()
    pending_borrowed = sum(
        (Decimal(str(d.payload.get("amount", 0))) for d in planning.pending(clinic, quarter).filter(kind=Kind.BORROW)),
        Decimal("0"),
    )
    offer = loans.quote(clinic, amount + pending_borrowed, duration, params, quarter)
    if not offer.accepted:
        form.add_error(None, f"La banque refuserait ce prêt : {offer.reason}")
        return form
    PendingDecision.objects.create(
        clinic=clinic, quarter=quarter, kind=Kind.BORROW, payload={"amount": str(amount), "duration_quarters": duration}
    )
    rate = f"{offer.annual_rate * 100:.2f}".replace(".", ",")
    messages.success(
        request,
        f"Demande d'emprunt de {amount:.0f} € enregistrée. Taux indicatif {rate} %, "
        "confirmé au déblocage selon votre situation à la résolution.",
    )
    return None


def _repay(request, clinic, quarter):
    loan_id = _object_id(request)
    loan = clinic.loans.filter(pk=loan_id, status=Loan.Status.ACTIVE).first() if loan_id else None
    if loan is None:
        messages.error(request, "Emprunt introuvable.")
    else:
        planning.replace_pending(clinic, quarter, Kind.REPAY_LOAN, "loan_id", loan.pk, {"loan_id": loan.pk})
        messages.success(request, "Remboursement anticipé programmé.")
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
    "format": _set_format,
    "borrow": _borrow,
    "repay": _repay,
}
