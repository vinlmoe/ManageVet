"""Extrait du Plan Comptable Général (PCG) utilisé par la simulation.

Simplifications assumées de la V1 :
- montants hors taxes ; la TVA (neutre pour une clinique assujettie) n'est
  pas encore comptabilisée ;
- salaires et charges sociales réglés dans le trimestre (pas de 421 / 431) ;
- fournisseurs et clients réglés comptant (pas de 401 / 411).
"""

CAPITAL = "101"
EQUIPMENT = "2154"  # Matériel industriel (matériel médical)
EQUIPMENT_DEPRECIATION = "28154"
STOCK_GOODS = "37"  # Stocks de marchandises
BANK = "512"

PURCHASES_GOODS = "607"  # Achats de marchandises
STOCK_VARIATION = "6037"  # Variation des stocks de marchandises
CONSUMABLES = "6022"  # Fournitures consommables
LEASING = "6122"  # Redevances de crédit-bail mobilier
RENT = "6132"  # Locations immobilières
FEES = "6226"  # Honoraires
OTHER_EXTERNAL = "628"  # Divers (énergie, assurances, télécom… regroupés)
SALARIES = "6411"  # Salaires, appointements
SEVERANCE = "6414"  # Indemnités et avantages divers
SOCIAL_CHARGES = "645"  # Charges de sécurité sociale et de prévoyance
BANK_INTEREST = "6615"  # Intérêts des comptes courants et découverts
DEPRECIATION_EXPENSE = "6811"  # Dotations aux amortissements

SERVICES_REVENUE = "706"  # Prestations de services
GOODS_REVENUE = "707"  # Ventes de marchandises

LABELS = {
    CAPITAL: "Capital",
    EQUIPMENT: "Matériel médical",
    EQUIPMENT_DEPRECIATION: "Amortissements du matériel",
    STOCK_GOODS: "Stocks de marchandises",
    BANK: "Banque",
    PURCHASES_GOODS: "Achats de marchandises",
    STOCK_VARIATION: "Variation des stocks de marchandises",
    CONSUMABLES: "Fournitures consommables",
    LEASING: "Crédit-bail mobilier",
    RENT: "Loyer",
    FEES: "Honoraires",
    OTHER_EXTERNAL: "Autres charges externes",
    SALARIES: "Salaires",
    SEVERANCE: "Indemnités de rupture",
    SOCIAL_CHARGES: "Charges sociales",
    BANK_INTEREST: "Agios",
    DEPRECIATION_EXPENSE: "Dotations aux amortissements",
    SERVICES_REVENUE: "Prestations de services",
    GOODS_REVENUE: "Ventes de marchandises",
}


def label(account: str) -> str:
    return LABELS.get(account, account)
