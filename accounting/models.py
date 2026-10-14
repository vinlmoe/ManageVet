from decimal import Decimal

from django.db import models

from . import pcg

MONEY = {"max_digits": 12, "decimal_places": 2}


class JournalEntry(models.Model):
    """Écriture comptable en partie double (somme débits = somme crédits)."""

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.CASCADE, related_name="journal_entries")
    quarter = models.PositiveSmallIntegerField("trimestre")
    label = models.CharField("libellé", max_length=200)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "écriture comptable"
        verbose_name_plural = "écritures comptables"
        ordering = ["quarter", "id"]

    def __str__(self):
        return f"T{self.quarter} – {self.label}"


class JournalLine(models.Model):
    entry = models.ForeignKey(JournalEntry, on_delete=models.CASCADE, related_name="lines")
    account = models.CharField("compte", max_length=10, db_index=True)
    debit = models.DecimalField(**MONEY, default=Decimal("0"))
    credit = models.DecimalField(**MONEY, default=Decimal("0"))

    class Meta:
        verbose_name = "ligne d'écriture"

    def __str__(self):
        return f"{self.account} D{self.debit} C{self.credit}"

    @property
    def account_label(self) -> str:
        return pcg.label(self.account)


class QuarterReport(models.Model):
    """Synthèse figée d'un trimestre résolu pour une clinique.

    Les montants financiers sont recalculés depuis le journal (source de
    vérité) ; les indicateurs d'activité viennent du moteur.
    """

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.CASCADE, related_name="reports")
    quarter = models.PositiveSmallIntegerField("trimestre")

    # Activité
    patients_dogs = models.PositiveIntegerField(default=0)
    patients_cats = models.PositiveIntegerField(default=0)
    acts = models.PositiveIntegerField("actes réalisés", default=0)
    capacity_ratio = models.FloatField("demande servie / demande", default=1.0)
    stock_fill_rate = models.FloatField("taux de service stock", default=1.0)
    market_share = models.FloatField("part de marché", default=0.0)
    reputation = models.FloatField(default=0.5)

    # Compte de résultat (HT)
    revenue_services = models.DecimalField(**MONEY, default=Decimal("0"))
    revenue_goods = models.DecimalField(**MONEY, default=Decimal("0"))
    purchases_goods = models.DecimalField(**MONEY, default=Decimal("0"))
    stock_variation = models.DecimalField(**MONEY, default=Decimal("0"))
    consumables = models.DecimalField(**MONEY, default=Decimal("0"))
    external_charges = models.DecimalField("loyer, honoraires, leasing, divers", **MONEY, default=Decimal("0"))
    payroll = models.DecimalField("salaires + charges sociales", **MONEY, default=Decimal("0"))
    depreciation = models.DecimalField(**MONEY, default=Decimal("0"))
    financial_charges = models.DecimalField(**MONEY, default=Decimal("0"))
    net_result = models.DecimalField("résultat", **MONEY, default=Decimal("0"))

    # Bilan simplifié en fin de trimestre
    cash_end = models.DecimalField(**MONEY, default=Decimal("0"))
    stock_value_end = models.DecimalField(**MONEY, default=Decimal("0"))
    debt_end = models.DecimalField("emprunts restant dus", **MONEY, default=Decimal("0"))
    equity_end = models.DecimalField("capitaux propres", **MONEY, default=Decimal("0"))
    clinic_format = models.CharField("format", max_length=10, blank=True)

    details = models.JSONField(default=dict, blank=True)

    class Meta:
        verbose_name = "rapport trimestriel"
        verbose_name_plural = "rapports trimestriels"
        ordering = ["clinic", "quarter"]
        constraints = [models.UniqueConstraint(fields=["clinic", "quarter"], name="unique_report_quarter")]

    def __str__(self):
        return f"{self.clinic} – T{self.quarter}"

    @property
    def revenue(self) -> Decimal:
        return self.revenue_services + self.revenue_goods

    @property
    def gross_margin_goods(self) -> Decimal:
        return self.revenue_goods - self.purchases_goods - self.stock_variation


class Loan(models.Model):
    """Emprunt bancaire amortissable à échéances trimestrielles constantes.

    Le taux est figé à la souscription. Les échéances commencent le
    trimestre qui suit le décaissement.
    """

    class Status(models.TextChoices):
        ACTIVE = "active", "En cours"
        REPAID = "repaid", "Remboursé"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.CASCADE, related_name="loans")
    principal = models.DecimalField("capital emprunté", **MONEY)
    annual_rate = models.DecimalField("taux annuel", max_digits=6, decimal_places=4)
    duration_quarters = models.PositiveSmallIntegerField("durée (trimestres)")
    start_quarter = models.PositiveSmallIntegerField("trimestre de décaissement")
    quarterly_payment = models.DecimalField("échéance trimestrielle", **MONEY)
    remaining_principal = models.DecimalField("capital restant dû", **MONEY)
    installments_paid = models.PositiveSmallIntegerField(default=0)
    status = models.CharField(max_length=6, choices=Status.choices, default=Status.ACTIVE)

    class Meta:
        verbose_name = "emprunt"
        ordering = ["start_quarter", "id"]

    def __str__(self):
        return f"{self.principal} € à {self.annual_rate:.2%} sur {self.duration_quarters} trim."

    @property
    def quarterly_rate(self) -> Decimal:
        return self.annual_rate / 4

    @property
    def end_quarter(self) -> int:
        return self.start_quarter + self.duration_quarters
