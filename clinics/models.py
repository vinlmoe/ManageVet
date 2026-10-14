from decimal import Decimal

from django.conf import settings
from django.db import models

MONEY = {"max_digits": 12, "decimal_places": 2}


# --- Catalogues de référence (communs à tous les serveurs) -------------------


class EquipmentType(models.Model):
    """Matériel acquérable : achat (immobilisé et amorti) ou leasing.

    Sert aussi pour les immobilisations qui ne sont pas au catalogue
    (``purchasable=False``), comme les aménagements d'un CHV.
    """

    code = models.SlugField(unique=True)
    name = models.CharField("nom", max_length=100)
    purchasable = models.BooleanField("proposé au catalogue", default=True)
    asset_account = models.CharField(
        "compte d'immobilisation", max_length=10, default="2154",
        help_text="2154 matériel ; 2135 installations et agencements.",
    )
    purchase_price = models.DecimalField("prix d'achat HT", **MONEY)
    lease_per_quarter = models.DecimalField("loyer de leasing / trimestre HT", **MONEY)
    useful_life_quarters = models.PositiveSmallIntegerField("durée d'amortissement (trimestres)", default=20)

    class Meta:
        verbose_name = "type de matériel"

    def __str__(self):
        return self.name


class ServiceType(models.Model):
    """Prestation vétérinaire (acte) avec ses paramètres économiques de référence."""

    code = models.SlugField(unique=True)
    name = models.CharField("nom", max_length=100)
    reference_price = models.DecimalField("prix de référence HT", **MONEY)
    frequency_per_animal = models.FloatField("actes par animal suivi et par trimestre")
    vet_minutes = models.PositiveSmallIntegerField("temps vétérinaire (min)")
    asv_minutes = models.PositiveSmallIntegerField("temps ASV (min)")
    consumables_cost = models.DecimalField("consommables par acte HT", **MONEY, default=Decimal("0"))
    required_equipment = models.ForeignKey(
        EquipmentType, null=True, blank=True, on_delete=models.PROTECT, related_name="services"
    )
    required_format = models.CharField(
        "réservée au format", max_length=10, blank=True, help_text="Ex. « chv » pour les urgences 24 h/24."
    )

    class Meta:
        verbose_name = "type de prestation"

    def __str__(self):
        return self.name

    @property
    def market_weight(self) -> float:
        """Poids de la prestation dans le panier moyen (sert aux indices)."""
        return float(self.reference_price) * self.frequency_per_animal


# --- Clinique d'un joueur -------------------------------------------------------


class Clinic(models.Model):
    class Format(models.TextChoices):
        STANDARD = "standard", "Clinique vétérinaire"
        LOW_COST = "low_cost", "Clinique low-cost"
        CHV = "chv", "Centre hospitalier vétérinaire (CHV)"

    server = models.ForeignKey("game.GameServer", on_delete=models.CASCADE, related_name="clinics")
    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="clinics")
    name = models.CharField("nom", max_length=100)
    x = models.PositiveSmallIntegerField()
    y = models.PositiveSmallIntegerField()

    # Trésorerie : valeur dénormalisée, toujours égale au solde du compte 512
    # (mise à jour exclusivement par accounting.services.post_entry).
    cash = models.DecimalField("trésorerie", **MONEY, default=Decimal("0"))
    rent_per_quarter = models.DecimalField("loyer / trimestre HT", **MONEY, default=Decimal("4500"))
    accountant_fee_per_quarter = models.DecimalField(
        "honoraires expert-comptable / trimestre HT", **MONEY, default=Decimal("1500")
    )
    reputation = models.FloatField("réputation (0-1)", default=0.5)
    format = models.CharField("format", max_length=10, choices=Format.choices, default=Format.STANDARD)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "clinique"
        constraints = [
            models.UniqueConstraint(fields=["server", "x", "y"], name="unique_clinic_cell"),
            models.UniqueConstraint(fields=["server", "owner"], name="one_clinic_per_player"),
        ]

    def __str__(self):
        return self.name


class ClinicService(models.Model):
    """Prix pratiqué et activation d'une prestation dans une clinique."""

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="services")
    service_type = models.ForeignKey(ServiceType, on_delete=models.PROTECT)
    price = models.DecimalField("prix HT", **MONEY)
    active = models.BooleanField("proposée", default=True)

    class Meta:
        verbose_name = "prestation de la clinique"
        constraints = [models.UniqueConstraint(fields=["clinic", "service_type"], name="unique_clinic_service")]

    def __str__(self):
        return f"{self.clinic} – {self.service_type}"


class StaffMember(models.Model):
    class Role(models.TextChoices):
        VET = "vet", "Vétérinaire"
        ASV = "asv", "ASV"

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="staff")
    role = models.CharField(max_length=3, choices=Role.choices)
    name = models.CharField("nom", max_length=100)
    gross_monthly_salary = models.DecimalField("salaire brut mensuel", **MONEY)
    hired_quarter = models.PositiveSmallIntegerField()
    left_quarter = models.PositiveSmallIntegerField(null=True, blank=True)

    class Meta:
        verbose_name = "membre du personnel"
        verbose_name_plural = "personnel"

    def __str__(self):
        return f"{self.name} ({self.get_role_display()})"

    @property
    def is_active(self) -> bool:
        return self.left_quarter is None


class ClinicEquipment(models.Model):
    class Mode(models.TextChoices):
        PURCHASE = "purchase", "Achat"
        LEASE = "lease", "Leasing"

    clinic = models.ForeignKey(Clinic, on_delete=models.CASCADE, related_name="equipment")
    equipment_type = models.ForeignKey(EquipmentType, on_delete=models.PROTECT)
    mode = models.CharField(max_length=8, choices=Mode.choices)
    acquired_quarter = models.PositiveSmallIntegerField()
    released_quarter = models.PositiveSmallIntegerField(null=True, blank=True)
    # Figés à l'acquisition : une hausse ultérieure du catalogue ne change pas le contrat.
    purchase_price = models.DecimalField(**MONEY, default=Decimal("0"))
    lease_per_quarter = models.DecimalField(**MONEY, default=Decimal("0"))
    accumulated_depreciation = models.DecimalField("amortissements cumulés", **MONEY, default=Decimal("0"))

    class Meta:
        verbose_name = "matériel de la clinique"

    def __str__(self):
        return f"{self.equipment_type} ({self.get_mode_display()})"

    @property
    def is_active(self) -> bool:
        return self.released_quarter is None
