"""Extrait du Plan Comptable Général (PCG) utilisé par la simulation.

Simplifications assumées de la V1 :
- montants hors taxes ; la TVA (neutre pour une clinique assujettie) n'est
  pas encore comptabilisée ;
- salaires et charges sociales réglés dans le trimestre (pas de 421 / 431) ;
- fournisseurs et clients réglés comptant (pas de 401 / 411).
"""

CAPITAL = "101"
LOANS = "164"  # Emprunts auprès des établissements de crédit
FITOUT = "2135"  # Installations générales, agencements, aménagements des constructions
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
LOAN_INTEREST = "6611"  # Intérêts des emprunts et dettes
BANK_INTEREST = "6615"  # Intérêts des comptes courants et découverts
OTHER_FINANCIAL = "668"  # Autres charges financières (indemnité de remboursement anticipé)
DEPRECIATION_EXPENSE = "6811"  # Dotations aux amortissements

SERVICES_REVENUE = "706"  # Prestations de services
GOODS_REVENUE = "707"  # Ventes de marchandises

LABELS = {
    CAPITAL: "Capital",
    LOANS: "Emprunts bancaires",
    FITOUT: "Agencements et installations",
    "28135": "Amortissements des agencements",
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
    LOAN_INTEREST: "Intérêts des emprunts",
    BANK_INTEREST: "Agios",
    OTHER_FINANCIAL: "Autres charges financières",
    DEPRECIATION_EXPENSE: "Dotations aux amortissements",
    SERVICES_REVENUE: "Prestations de services",
    GOODS_REVENUE: "Ventes de marchandises",
}


def label(account: str) -> str:
    return LABELS.get(account, account)


def depreciation_account(asset_account: str) -> str:
    """Compte d'amortissement associé : 2154 -> 28154, 2135 -> 28135."""
    return "28" + asset_account[1:]
