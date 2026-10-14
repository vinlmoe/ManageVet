"""Aide au choix de l'emplacement d'une clinique.

Pour chaque case de la carte, on calcule ce qu'un étudiant doit pouvoir
comparer avant de s'installer :

- la patientèle potentielle dans un rayon de chalandise ;
- la concurrence (clinique la plus proche, nombre de cliniques dans le rayon) ;
- le loyer, plus élevé là où la population est dense.

Le loyer dépendant de l'emplacement crée l'arbitrage pédagogique : un centre
ville offre beaucoup de clients potentiels mais coûte cher et attire la
concurrence ; la campagne est bon marché mais peu peuplée.
"""

import math
from dataclasses import dataclass
from decimal import Decimal

CATCHMENT_KM = 6.0
BASE_RENT = Decimal("3000")  # loyer trimestriel HT en zone rurale
MAX_URBAN_SURCHARGE = Decimal("3000")  # supplément au cœur de la zone la plus dense


@dataclass(frozen=True)
class CellInsight:
    x: int
    y: int
    animals: int  # animaux sur la case
    catchment: int  # animaux dans le rayon de chalandise
    nearest_competitor_km: float | None
    competitors_nearby: int
    rent: Decimal


def cell_insights(server) -> dict[tuple[int, int], CellInsight]:
    cell_km = server.get_parameters().cell_size_km
    radius_cells = math.ceil(CATCHMENT_KM / cell_km)
    animals = {(z.x, z.y): z.dogs + z.cats for z in server.zones.all()}
    clinics = list(server.clinics.values_list("x", "y"))

    catchment = {}
    for x in range(server.grid_width):
        for y in range(server.grid_height):
            total = 0
            for dx in range(-radius_cells, radius_cells + 1):
                for dy in range(-radius_cells, radius_cells + 1):
                    if math.hypot(dx, dy) * cell_km <= CATCHMENT_KM:
                        total += animals.get((x + dx, y + dy), 0)
            catchment[(x, y)] = total
    max_catchment = max(catchment.values(), default=0) or 1

    insights = {}
    for (x, y), total in catchment.items():
        distances = [math.hypot(x - cx, y - cy) * cell_km for cx, cy in clinics]
        rent = BASE_RENT + MAX_URBAN_SURCHARGE * Decimal(total) / Decimal(max_catchment)
        insights[(x, y)] = CellInsight(
            x=x,
            y=y,
            animals=animals.get((x, y), 0),
            catchment=total,
            nearest_competitor_km=round(min(distances), 1) if distances else None,
            competitors_nearby=sum(1 for d in distances if d <= CATCHMENT_KM),
            rent=(rent / 50).quantize(Decimal("1")) * 50,  # arrondi à 50 €
        )
    return insights


def rent_for_cell(server, x: int, y: int) -> Decimal:
    return cell_insights(server)[(x, y)].rent
