"""Génération de la carte : une grille abstraite de cases.

Chaque case porte une population d'animaux. La densité est la somme d'un
fond rural et de quelques « villes » (gaussiennes) placées aléatoirement :
on obtient sans aucune donnée géographique réelle des zones denses, des
zones périurbaines et une campagne peu peuplée. Les distances sont
euclidiennes, converties en km via ``cell_size_km``.
"""

import math
import random

from .models import GameServer, PopulationZone


def generate_zones(
    server: GameServer,
    towns: int = 3,
    rural_animals: int = 12,
    town_peak_animals: int = 400,
    town_radius_cells: float = 2.5,
) -> list[PopulationZone]:
    rng = random.Random(server.seed)
    centers = [
        (
            rng.uniform(0.15, 0.85) * server.grid_width,
            rng.uniform(0.15, 0.85) * server.grid_height,
            rng.uniform(0.5, 1.0),  # taille relative de la ville
        )
        for _ in range(towns)
    ]
    zones = []
    for x in range(server.grid_width):
        for y in range(server.grid_height):
            urban = sum(
                size * math.exp(-((x - cx) ** 2 + (y - cy) ** 2) / (2 * town_radius_cells**2))
                for cx, cy, size in centers
            )
            animals = rural_animals * rng.uniform(0.6, 1.4) + town_peak_animals * urban
            cat_share = 0.5 + 0.15 * min(urban, 1.0)  # plus de chats en ville
            zones.append(
                PopulationZone(
                    server=server,
                    x=x,
                    y=y,
                    dogs=round(animals * (1 - cat_share)),
                    cats=round(animals * cat_share),
                    # Les zones rurales sont un peu plus sensibles au prix.
                    price_sensitivity=round(1.2 - 0.4 * min(urban, 1.0), 3),
                )
            )
    PopulationZone.objects.filter(server=server).delete()
    return PopulationZone.objects.bulk_create(zones)
