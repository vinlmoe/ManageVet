"""Convention collective des établissements de soins vétérinaires (rémunérations minimales).

Personnel non vétérinaire : IDCC 1875 (cabinets et cliniques vétérinaires).
Vétérinaires salariés : ex-IDCC 2564, devenu l'annexe VII de la convention
1875 (accord de fusion de 2019). Une convention fusionnée (IDCC 3256) est
annoncée pour 2027.

Salaire minimum mensuel brut (151,67 h) = coefficient x valeur du point.
Valeur du point 2026 : 17,96 € (avenant n° 91 du 6 janvier 2026), réglable
via le paramètre de simulation ``ccn_point_value``.

ATTENTION : coefficients, libellés et taux de majoration repris de sources
secondaires (sites spécialisés en paie), le texte officiel n'ayant pas pu
être consulté. À vérifier sur Légifrance avant usage pédagogique.
"""

from decimal import ROUND_HALF_UP, Decimal

MONTHLY_HOURS = Decimal("151.67")

# Échelons proposés au recrutement : (libellé, coefficient)
VET_LEVELS = {
    2: ("Échelon 2 – cadre débutant", 152),
    3: ("Échelon 3 – cadre confirmé A", 182),
    4: ("Échelon 4 – cadre confirmé B", 210),
    5: ("Échelon 5 – cadre spécialisé", 240),
}
ASV_LEVELS = {
    3: ("Échelon 3 – auxiliaire vétérinaire qualifié (AVQ)", 110),
    4: ("Échelon 4 – auxiliaire vétérinaire confirmé", 113),
    5: ("Échelon 5 – auxiliaire spécialisé vétérinaire (ASV)", 120),
}
LEVELS = {"vet": VET_LEVELS, "asv": ASV_LEVELS}
DEFAULT_LEVEL = {"vet": 3, "asv": 5}
SPECIALIST_LEVEL = 5  # un vétérinaire spécialiste relève de l'échelon « cadre spécialisé »


def coefficient(role: str, level: int) -> int:
    try:
        return LEVELS[role][level][1]
    except KeyError:
        raise ValueError(f"Échelon {level} inconnu pour le rôle {role}.")


def minimum_salary(role: str, level: int, point_value) -> Decimal:
    value = Decimal(coefficient(role, level)) * Decimal(str(point_value))
    return value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)


def hourly_rate(gross_monthly_salary) -> Decimal:
    return Decimal(gross_monthly_salary) / MONTHLY_HOURS


def level_label(role: str, level: int) -> str:
    return LEVELS.get(role, {}).get(level, (f"Échelon {level}", 0))[0]


# --- Effets de l'échelon dans le jeu (hypothèses, voir SimulationParameters) ---------

QUALIFIED_ASV_LEVEL = 5  # seules les ASV titrées comptent comme « personnes qualifiées » du CHV


def productivity(role: str, level: int, params) -> float:
    """Coefficient appliqué aux heures facturables selon l'échelon (1,0 = référence)."""
    return float(getattr(params, f"{role}_productivity_level{level}", 1.0))


def experience_score(role: str, level: int) -> float:
    """Expérience relative dans [-1, 1] : -1 au plus bas échelon proposé, +1 au plus haut."""
    levels = sorted(LEVELS[role])
    low, high = levels[0], levels[-1]
    mid = (low + high) / 2
    return max(-1.0, min(1.0, (level - mid) / ((high - low) / 2)))
