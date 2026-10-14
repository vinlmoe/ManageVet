from django.db import models


class PendingDecision(models.Model):
    """Décision prise par un joueur pendant la semaine, appliquée à la résolution.

    Les décisions sont stockées telles quelles (type + charge utile JSON) et
    ne sont validées qu'au moment de la résolution : un joueur peut donc
    changer d'avis (annuler) tant que le trimestre n'est pas calculé.
    """

    class Kind(models.TextChoices):
        SET_SERVICE = "set_service", "Modifier une prestation (prix / activation)"
        SET_PRODUCT = "set_product", "Modifier une gamme (prix / niveau de réassort)"
        HIRE = "hire", "Recruter"
        DISMISS = "dismiss", "Se séparer d'un salarié"
        ACQUIRE_EQUIPMENT = "acquire_equipment", "Acheter / louer du matériel"
        RELEASE_EQUIPMENT = "release_equipment", "Résilier un leasing"

    class Status(models.TextChoices):
        PENDING = "pending", "En attente"
        APPLIED = "applied", "Appliquée"
        REJECTED = "rejected", "Rejetée"
        CANCELLED = "cancelled", "Annulée"

    clinic = models.ForeignKey("clinics.Clinic", on_delete=models.CASCADE, related_name="decisions")
    quarter = models.PositiveSmallIntegerField("trimestre visé")
    kind = models.CharField(max_length=20, choices=Kind.choices)
    payload = models.JSONField(default=dict)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.PENDING)
    message = models.CharField(max_length=255, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    resolved_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "décision"
        ordering = ["created_at", "id"]

    def __str__(self):
        return f"{self.clinic} T{self.quarter} {self.get_kind_display()} [{self.get_status_display()}]"
