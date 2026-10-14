"""Services comptables : passation d'écritures et soldes de comptes."""

from decimal import ROUND_HALF_UP, Decimal

from django.db import transaction
from django.db.models import F, Sum

from . import pcg
from .models import JournalEntry, JournalLine

CENT = Decimal("0.01")


class UnbalancedEntry(ValueError):
    pass


def money(value) -> Decimal:
    """Arrondi monétaire au centime (arrondi commercial)."""
    if not isinstance(value, Decimal):
        value = Decimal(repr(float(value)))
    return value.quantize(CENT, rounding=ROUND_HALF_UP)


@transaction.atomic
def post_entry(clinic, quarter: int, label: str, lines: list[tuple[str, Decimal, Decimal]]):
    """Enregistre une écriture équilibrée et met à jour la trésorerie de la clinique.

    ``lines`` : liste de tuples ``(compte, débit, crédit)``. Les lignes nulles
    sont ignorées ; si plus rien ne reste, aucune écriture n'est créée.
    """
    clean = [(acc, money(d), money(c)) for acc, d, c in lines]
    clean = [(acc, d, c) for acc, d, c in clean if d or c]
    if not clean:
        return None
    if any(d < 0 or c < 0 for _, d, c in clean):
        raise UnbalancedEntry(f"Montant négatif dans l'écriture « {label} »")
    total_debit = sum(d for _, d, _ in clean)
    total_credit = sum(c for _, _, c in clean)
    if total_debit != total_credit:
        raise UnbalancedEntry(f"Écriture « {label} » déséquilibrée : D={total_debit} C={total_credit}")

    entry = JournalEntry.objects.create(clinic=clinic, quarter=quarter, label=label)
    JournalLine.objects.bulk_create(
        [JournalLine(entry=entry, account=acc, debit=d, credit=c) for acc, d, c in clean]
    )
    bank_delta = sum(d - c for acc, d, c in clean if acc == pcg.BANK)
    if bank_delta:
        type(clinic).objects.filter(pk=clinic.pk).update(cash=F("cash") + bank_delta)
        clinic.refresh_from_db(fields=["cash"])
    return entry


def account_balance(clinic, prefix: str, quarter: int | None = None, up_to_quarter: int | None = None) -> Decimal:
    """Solde débiteur (débit - crédit) des comptes commençant par ``prefix``."""
    qs = JournalLine.objects.filter(entry__clinic=clinic, account__startswith=prefix)
    if quarter is not None:
        qs = qs.filter(entry__quarter=quarter)
    if up_to_quarter is not None:
        qs = qs.filter(entry__quarter__lte=up_to_quarter)
    agg = qs.aggregate(d=Sum("debit"), c=Sum("credit"))
    return (agg["d"] or Decimal("0")) - (agg["c"] or Decimal("0"))


def income_statement(clinic, quarter: int) -> dict[str, Decimal]:
    """Compte de résultat d'un trimestre, regroupé par grands postes."""

    def charge(*accounts):
        return sum((account_balance(clinic, a, quarter=quarter) for a in accounts), Decimal("0"))

    def product(*accounts):
        return -charge(*accounts)

    data = {
        "revenue_services": product(pcg.SERVICES_REVENUE),
        "revenue_goods": product(pcg.GOODS_REVENUE),
        "purchases_goods": charge(pcg.PURCHASES_GOODS),
        "stock_variation": charge(pcg.STOCK_VARIATION),
        "consumables": charge(pcg.CONSUMABLES),
        "external_charges": charge(pcg.LEASING, pcg.RENT, pcg.FEES, pcg.OTHER_EXTERNAL),
        "payroll": charge(pcg.SALARIES, pcg.SEVERANCE, pcg.SOCIAL_CHARGES),
        "depreciation": charge(pcg.DEPRECIATION_EXPENSE),
        "financial_charges": charge(pcg.LOAN_INTEREST, pcg.BANK_INTEREST, pcg.OTHER_FINANCIAL),
    }
    # Résultat = produits (classe 7) - charges (classe 6), calculé sur tout le journal
    # pour ne rien oublier si un compte est ajouté plus tard.
    data["net_result"] = -account_balance(clinic, "7", quarter=quarter) - account_balance(
        clinic, "6", quarter=quarter
    )
    return data


def equity(clinic, up_to_quarter: int | None = None) -> Decimal:
    """Capitaux propres : capital + résultats cumulés (pas encore de clôture annuelle)."""
    capital = -account_balance(clinic, "10", up_to_quarter=up_to_quarter)
    results = -account_balance(clinic, "7", up_to_quarter=up_to_quarter) - account_balance(
        clinic, "6", up_to_quarter=up_to_quarter
    )
    return capital + results


def outstanding_debt(clinic) -> Decimal:
    return -account_balance(clinic, pcg.LOANS)
