from decimal import Decimal

from django import forms

from accounting.loans import DURATIONS, MIN_AMOUNT
from clinics import ccn
from clinics.models import Clinic, ClinicEquipment, Specialty, StaffMember

PRICE = {"min_value": Decimal("0"), "max_digits": 10, "decimal_places": 2}


class ServicesForm(forms.Form):
    """Tableau des prestations : un prix et une case « proposée » par ligne."""

    def __init__(self, *args, clinic_services, plan, operable_codes, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []
        for cs in clinic_services:
            code = cs.service_type.code
            price = forms.DecimalField(label=cs.service_type.name, initial=plan[code]["price"], **PRICE)
            active = forms.BooleanField(
                required=False, initial=plan[code]["active"], disabled=code not in operable_codes
            )
            self.fields[f"price_{code}"] = price
            self.fields[f"active_{code}"] = active
            self.rows.append((cs, code))

    def bound_rows(self):
        for cs, code in self.rows:
            yield cs, self[f"price_{code}"], self[f"active_{code}"]


class ProductsForm(forms.Form):
    def __init__(self, *args, stock_items, plan, **kwargs):
        super().__init__(*args, **kwargs)
        self.rows = []
        for item in stock_items:
            code = item.product_line.code
            self.fields[f"price_{code}"] = forms.DecimalField(
                label=item.product_line.name, initial=plan[code]["retail_price"], **PRICE
            )
            self.fields[f"target_{code}"] = forms.IntegerField(
                min_value=0, max_value=100_000, initial=plan[code]["target_quantity"]
            )
            self.rows.append((item, code))

    def bound_rows(self):
        for item, code in self.rows:
            yield item, self[f"price_{code}"], self[f"target_{code}"]


class HireForm(forms.Form):
    """Recrutement selon la grille de la convention collective."""

    role = forms.ChoiceField(label="Poste", choices=StaffMember.Role.choices)
    level = forms.TypedChoiceField(
        label="Échelon",
        coerce=int,
        choices=[
            ("Vétérinaire", [(lvl, label) for lvl, (label, _) in ccn.VET_LEVELS.items()]),
            ("Auxiliaire", [(lvl, label) for lvl, (label, _) in ccn.ASV_LEVELS.items()]),
        ],
        initial=ccn.DEFAULT_LEVEL["vet"],
    )
    specialty = forms.ChoiceField(
        label="Spécialité", choices=[("", "Aucune (généraliste)")] + list(Specialty.choices), required=False
    )
    name = forms.CharField(label="Nom", max_length=100)
    gross_monthly_salary = forms.DecimalField(
        label="Salaire brut mensuel (€)", min_value=Decimal("0"), max_value=Decimal("20000"),
        max_digits=10, decimal_places=2,
    )

    def __init__(self, *args, point_value, **kwargs):
        super().__init__(*args, **kwargs)
        self.point_value = point_value

    def clean(self):
        data = super().clean()
        role, level, specialty = data.get("role"), data.get("level"), data.get("specialty")
        salary = data.get("gross_monthly_salary")
        if role is None or level is None:
            return data
        if level not in ccn.LEVELS[role]:
            self.add_error("level", "Cet échelon ne correspond pas au poste choisi.")
            return data
        if specialty and role != StaffMember.Role.VET:
            self.add_error("specialty", "Seul un vétérinaire peut être spécialiste.")
        if specialty and level != ccn.SPECIALIST_LEVEL:
            self.add_error("level", "Un vétérinaire spécialiste relève de l'échelon 5 (cadre spécialisé).")
        minimum = ccn.minimum_salary(role, level, self.point_value)
        if salary is not None and salary < minimum:
            self.add_error(
                "gross_monthly_salary",
                f"Minimum conventionnel pour « {ccn.level_label(role, level)} » : {minimum} € brut.",
            )
        return data


class AcquireForm(forms.Form):
    equipment = forms.SlugField()
    mode = forms.ChoiceField(choices=ClinicEquipment.Mode.choices)


class IdForm(forms.Form):
    """Sert aux actions portant sur un objet existant (départ, résiliation, annulation)."""

    object_id = forms.IntegerField(min_value=1)


class FormatForm(forms.Form):
    format = forms.ChoiceField(label="Format", choices=Clinic.Format.choices, widget=forms.RadioSelect)


class BorrowForm(forms.Form):
    amount = forms.DecimalField(
        label="Montant (€)", min_value=MIN_AMOUNT, max_value=Decimal("1000000"), max_digits=10, decimal_places=0
    )
    duration_quarters = forms.TypedChoiceField(
        label="Durée",
        coerce=int,
        choices=[(d, f"{d // 4} an{'s' if d > 4 else ''}") for d in DURATIONS],
        initial=20,
    )
