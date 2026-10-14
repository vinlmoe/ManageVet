"""Emprunts bancaires : offre de la banque, décaissement, échéances, remboursement anticipé.

Échéance constante (annuité) pour un capital ``P``, un taux trimestriel ``r``
et ``n`` trimestres :  a = P * r / (1 - (1 + r) ** -n).
Chaque échéance se décompose en intérêts (6611) et amortissement du capital (164).
"""

from dataclasses import dataclass
from decimal import Decimal

from clinics.services import OperationError

from . import pcg
from .models import Loan
from .services import equity, money, outstanding_debt, post_entry

MIN_AMOUNT = Decimal("5000")
DURATIONS = (4, 8, 12, 20, 28, 40)  # 1, 2, 3, 5, 7 et 10 ans


@dataclass(frozen=True)
class LoanQuote:
    accepted: bool
    reason: str
    annual_rate: Decimal
    quarterly_payment: Decimal
    max_amount: Decimal


def annuity(principal: Decimal, annual_rate: Decimal, quarters: int) -> Decimal:
    r = float(annual_rate) / 4
    p = float(principal)
    if r == 0:
        return money(p / quarters)
    return money(p * r / (1 - (1 + r) ** -quarters))


def borrowing_capacity(clinic, params) -> Decimal:
    """Montant maximal qu'une banque accepterait de prêter en plus de la dette actuelle."""
    eq = equity(clinic)
    if eq <= 0:
        return Decimal("0")
    ceiling = min(Decimal(str(params.loan_max_amount)), eq * Decimal(str(params.loan_max_leverage)))
    return max(Decimal("0"), money(ceiling - outstanding_debt(clinic)))


def quote(clinic, amount: Decimal, duration: int, params, quarter: int) -> LoanQuote:
    amount = Decimal(str(amount))
    capacity = borrowing_capacity(clinic, params)
    eq = equity(clinic)
    debt = outstanding_debt(clinic)
    last = clinic.reports.filter(quarter=quarter - 1).first()
    leverage = (debt + amount) / eq if eq > 0 else Decimal("0")
    rate = (
        Decimal(str(params.loan_base_rate_annual))
        + Decimal(str(params.loan_risk_premium_per_leverage)) * leverage
        + (Decimal(str(params.loan_loss_premium)) if last and last.net_result < 0 else Decimal("0"))
    ).quantize(Decimal("0.0001"))

    def refuse(reason):
        return LoanQuote(False, reason, rate, Decimal("0"), capacity)

    if duration not in DURATIONS:
        return refuse("Durée non proposée par la banque.")
    if amount < MIN_AMOUNT:
        return refuse(f"Montant minimum : {MIN_AMOUNT} €.")
    if eq <= 0:
        return refuse("Capitaux propres négatifs : la banque refuse tout nouveau prêt.")
    if amount > capacity:
        return refuse(
            f"Capacité d'emprunt dépassée : {capacity:.0f} € au plus "
            f"(dette totale ≤ {params.loan_max_leverage:g} × capitaux propres, plafond {params.loan_max_amount:.0f} €)."
        )
    return LoanQuote(True, "", rate, annuity(amount, rate, duration), capacity)


def borrow(clinic, amount: Decimal, duration: int, params) -> Loan:
    quarter = clinic.server.current_quarter
    offer = quote(clinic, amount, duration, params, quarter)
    if not offer.accepted:
        raise OperationError(f"Prêt refusé : {offer.reason}")
    amount = money(amount)
    loan = Loan.objects.create(
        clinic=clinic,
        principal=amount,
        annual_rate=offer.annual_rate,
        duration_quarters=duration,
        start_quarter=quarter,
        quarterly_payment=offer.quarterly_payment,
        remaining_principal=amount,
    )
    post_entry(clinic, quarter, f"Déblocage d'emprunt ({offer.annual_rate:.2%})", [(pcg.BANK, amount, 0), (pcg.LOANS, 0, amount)])
    return loan


def pay_installments(clinic, quarter: int) -> Decimal:
    """Prélève les échéances du trimestre ; renvoie le total décaissé."""
    total = Decimal("0")
    for loan in clinic.loans.filter(status=Loan.Status.ACTIVE, start_quarter__lt=quarter):
        interest = money(loan.remaining_principal * loan.quarterly_rate)
        last = loan.installments_paid + 1 >= loan.duration_quarters
        principal = loan.remaining_principal if last else min(loan.quarterly_payment - interest, loan.remaining_principal)
        post_entry(
            clinic,
            quarter,
            f"Échéance d'emprunt {loan.installments_paid + 1}/{loan.duration_quarters}",
            [(pcg.LOAN_INTEREST, interest, 0), (pcg.LOANS, principal, 0), (pcg.BANK, 0, interest + principal)],
        )
        loan.remaining_principal -= principal
        loan.installments_paid += 1
        if loan.remaining_principal <= 0 or last:
            loan.status = Loan.Status.REPAID
        loan.save(update_fields=["remaining_principal", "installments_paid", "status"])
        total += interest + principal
    return total


def repay_early(clinic, loan_id: int, params) -> Loan:
    loan = clinic.loans.filter(pk=loan_id, status=Loan.Status.ACTIVE).first()
    if loan is None:
        raise OperationError("Emprunt introuvable ou déjà remboursé.")
    penalty = early_repayment_penalty(loan, params)
    quarter = clinic.server.current_quarter
    post_entry(
        clinic,
        quarter,
        "Remboursement anticipé d'emprunt",
        [(pcg.LOANS, loan.remaining_principal, 0), (pcg.OTHER_FINANCIAL, penalty, 0), (pcg.BANK, 0, loan.remaining_principal + penalty)],
    )
    loan.remaining_principal = Decimal("0")
    loan.status = Loan.Status.REPAID
    loan.save(update_fields=["remaining_principal", "status"])
    return loan


def early_repayment_penalty(loan: Loan, params) -> Decimal:
    return money(loan.remaining_principal * Decimal(str(params.loan_early_repayment_penalty)))
