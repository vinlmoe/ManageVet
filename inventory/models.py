from decimal import Decimal

from django.db import models

MONEY = {"max_digits": 12, "decimal_places": 2}


class ProductLine(models.Model):
    """Gamme de produits (aliments, antiparasitaires) vendue au comptoir.

    On raisonne par gamme et non par référence pour éviter la micro-gestion.
    """

    class Species(models.TextChoices):
        DOG = "dog", "Chien"
        CAT = "cat", "Chat"
        ALL = "all", "Chien et chat"

    code = models.SlugField(unique=True)
    name = models.CharField("nom", max_length=100)
    species = models.CharField(max_length=3, choices=Species.choices)
    unit_cost = models.DecimalField("coût d'achat unitaire HT", **MONEY)
    recommended_price = models.DecimalField("prix de vente conseillé HT", **MONEY)
    units_per_animal = models.FloatField("unités vendues par animal suivi et par trimestre (au prix conseillé)")

    class Meta:
        verbose_name = "gamme de produits"
        verbose_name_plural = "gammes de produits"

    def __str__(self):
        return self.name


class StockItem(models.Model):
    """Stock d'une gamme dans une clinique.

    Réassort automatique : en début de résolution, le stock est recomplété
    jusqu'à ``target_quantity`` (niveau cible choisi par le joueur). Le
    stock est valorisé au coût moyen unitaire pondéré (CMUP).
    """

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.CASCADE, related_name="stock")
    product_line = models.ForeignKey(ProductLine, on_delete=models.PROTECT)
    quantity = models.PositiveIntegerField("quantité en stock", default=0)
    average_unit_cost = models.DecimalField("CMUP", **MONEY, default=Decimal("0"))
    target_quantity = models.PositiveIntegerField("niveau de réassort", default=0)
    retail_price = models.DecimalField("prix de vente HT", **MONEY)

    class Meta:
        verbose_name = "stock"
        constraints = [models.UniqueConstraint(fields=["clinic", "product_line"], name="unique_stock_line")]

    def __str__(self):
        return f"{self.clinic} – {self.product_line}: {self.quantity}"

    @property
    def value(self) -> Decimal:
        return (self.average_unit_cost * self.quantity).quantize(Decimal("0.01"))
