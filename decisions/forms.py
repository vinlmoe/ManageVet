from decimal import Decimal

from django import forms

from clinics.models import ClinicEquipment, StaffMember
from clinics.services import MIN_GROSS_MONTHLY_SALARY

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
    role = forms.ChoiceField(label="Poste", choices=StaffMember.Role.choices)
    name = forms.CharField(label="Nom", max_length=100)
    gross_monthly_salary = forms.DecimalField(
        label="Salaire brut mensuel (€)", min_value=MIN_GROSS_MONTHLY_SALARY, max_value=Decimal("20000"),
        max_digits=10, decimal_places=2,
    )


class AcquireForm(forms.Form):
    equipment = forms.SlugField()
    mode = forms.ChoiceField(choices=ClinicEquipment.Mode.choices)


class IdForm(forms.Form):
    """Sert aux actions portant sur un objet existant (départ, résiliation, annulation)."""

    object_id = forms.IntegerField(min_value=1)
