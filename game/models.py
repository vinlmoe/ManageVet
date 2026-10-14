from datetime import timedelta

from django.db import models
from django.utils import timezone


class GameServer(models.Model):
    """Une partie : une carte commune, une dizaine de cliniques en concurrence.

    Un trimestre de jeu = une semaine réelle. ``current_quarter`` est le
    trimestre en cours de décision ; il est résolu par la tâche hebdomadaire
    puis incrémenté.
    """

    class Status(models.TextChoices):
        DRAFT = "draft", "En préparation"
        RUNNING = "running", "En cours"
        FINISHED = "finished", "Terminée"

    name = models.CharField("nom", max_length=100)
    status = models.CharField(max_length=10, choices=Status.choices, default=Status.DRAFT)
    grid_width = models.PositiveSmallIntegerField("largeur de la carte (cases)", default=20)
    grid_height = models.PositiveSmallIntegerField("hauteur de la carte (cases)", default=20)
    current_quarter = models.PositiveSmallIntegerField("trimestre en cours", default=1)
    total_quarters = models.PositiveSmallIntegerField("nombre de trimestres", default=4)
    resolution_interval_days = models.PositiveSmallIntegerField(default=7)
    next_resolution_at = models.DateTimeField("prochaine résolution", null=True, blank=True)
    seed = models.PositiveIntegerField("graine aléatoire", default=42)
    parameters = models.JSONField(
        "surcharges des paramètres de simulation",
        default=dict,
        blank=True,
        help_text="Ex. {\"demand_multiplier\": 0.8} pour un stress test.",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "serveur de jeu"
        verbose_name_plural = "serveurs de jeu"

    def __str__(self):
        return self.name

    @property
    def year(self) -> int:
        return (self.current_quarter - 1) // 4 + 1

    @property
    def quarter_in_year(self) -> int:
        return (self.current_quarter - 1) % 4 + 1

    def start(self):
        self.status = self.Status.RUNNING
        self.next_resolution_at = timezone.now() + timedelta(days=self.resolution_interval_days)
        self.save(update_fields=["status", "next_resolution_at"])

    def get_parameters(self):
        from engine.parameters import SimulationParameters

        return SimulationParameters.from_overrides(self.parameters)


class PopulationZone(models.Model):
    """Une case de la carte et la patientèle potentielle qui y vit.

    La patientèle n'est pas modélisée individuellement : chaque case agrège
    un nombre de chiens et de chats dont les propriétaires arbitrent entre
    les cliniques à chaque trimestre.
    """

    server = models.ForeignKey(GameServer, on_delete=models.CASCADE, related_name="zones")
    x = models.PositiveSmallIntegerField()
    y = models.PositiveSmallIntegerField()
    dogs = models.PositiveIntegerField("chiens", default=0)
    cats = models.PositiveIntegerField("chats", default=0)
    price_sensitivity = models.FloatField(
        "sensibilité au prix", default=1.0, help_text="1.0 = moyenne ; >1 zone plus modeste."
    )

    class Meta:
        verbose_name = "zone de population"
        constraints = [models.UniqueConstraint(fields=["server", "x", "y"], name="unique_zone_cell")]

    def __str__(self):
        return f"({self.x},{self.y}) {self.dogs}🐕 {self.cats}🐈"
