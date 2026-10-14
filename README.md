# ManageVet — Simulation économique de clinique vétérinaire

Jeu de gestion à visée pédagogique pour étudiants vétérinaires : chaque joueur
dirige une clinique pendant une année simulée découpée en trimestres, en
concurrence avec une dizaine d'autres cliniques sur une carte commune.

- **1 trimestre de jeu = 1 semaine réelle.** Les joueurs saisissent leurs décisions
  pendant la semaine ; une tâche planifiée résout le trimestre en un seul calcul.
- **Patientèle simulée** répartie sur une grille, qui choisit sa clinique selon le
  prix, la distance, l'offre de services et la réputation (modèle logit).
- **Comptabilité en partie double** suivant le Plan Comptable Général (706, 707,
  607, 6037, 641, 645, 6132, 6226, 6122, 6811…).

Le document de conception détaillé (modèle de données, équations du moteur,
simplifications) est dans [`docs/conception.tex`](docs/conception.tex).

## Démarrage rapide

```bash
python -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt
python manage.py migrate
python manage.py createsuperuser
python manage.py seed_demo --clinics 10          # partie de démo avec 10 cliniques « bots »
python manage.py resolve_quarters --server 1 --quarters 4
python manage.py runserver                        # http://127.0.0.1:8000/
```

## Côté joueur

1. **Inscription** : l'étudiant crée son compte sur `/accounts/signup/`
   (désactivable avec `DJANGO_SIGNUP_ENABLED=0`).
2. **Installation** : depuis la liste des parties, « Rejoindre » ouvre la carte.
   Pour chaque case libre, il voit la patientèle à moins de 6 km, la
   concurrence proche et le loyer, qui dépend de la densité (3 000 à
   6 000 € HT par trimestre). Il nomme sa clinique et l'installe.
3. **Décisions** : sur l'écran de décisions (`/clinics/<id>/decisions/`) :

- tarifs des prestations et activation de l'offre de soins ;
- prix de vente et niveau de réassort des gammes de produits ;
- recrutements et départs (vétérinaires, ASV) ;
- achat ou leasing de matériel, résiliation d'un leasing ;
- **positionnement** : clinique standard, **low-cost** (actes de base,
  standardisés, moins chers à produire) ou **CHV** (urgences, hospitalisation,
  chirurgie spécialisée ; exige effectif et plateau technique minimum,
  travaux d'aménagement, surcoûts de permanence) ;
- **emprunts bancaires** : taux selon l'endettement et les résultats,
  échéances trimestrielles constantes, remboursement anticipé avec indemnité ;
- liste des décisions en attente avec leur impact estimé sur la trésorerie,
  et possibilité de les annuler jusqu'à la résolution.

Une nouvelle saisie sur la même prestation ou la même gamme remplace la
décision en attente. Un joueur ne voit que sa propre clinique (comptes et
décisions) ; les comptes `is_staff` (équipe pédagogique) voient tout et ont
accès à l'admin.

L'enseignant règle dans l'admin, pour chaque partie : inscriptions ouvertes
ou non, nombre maximal de cliniques et **code d'accès** à communiquer à la
promotion (vide = partie ouverte à tous). Pour une démo :
`python manage.py seed_demo --free-slots 3 --join-code VETO26`.

## Stress tests

Chaque serveur accepte des surcharges de paramètres (voir
`engine/parameters.py`) :

```bash
python manage.py seed_demo --name "Choc de demande" --param demand_multiplier=0.7
python manage.py seed_demo --name "Inflation fournisseurs" --param purchase_cost_multiplier=1.25
python manage.py seed_demo --name "Charges sociales +" --param employer_contribution_rate=0.50
python manage.py seed_demo --name "Taux à 7 %" --param loan_base_rate_annual=0.07
python manage.py seed_demo --name "CHV plus exigeant" --param chv_min_vets=6
```

## Mise à jour d'une installation existante

```bash
python manage.py migrate
python manage.py seed_catalog   # nouvelles prestations / immobilisations, ajoutées aux cliniques existantes
```

La résolution est déterministe (graine du serveur) : mêmes décisions ⇒ mêmes résultats.

## Planification hebdomadaire

`python manage.py resolve_quarters` (sans option) résout tous les serveurs dont
l'échéance `next_resolution_at` est passée. Exemple de crontab (dimanche 20 h) :

```
0 20 * * 0  cd /srv/managevet && .venv/bin/python manage.py resolve_quarters
```

## Arborescence

```
config/        Configuration Django (settings, urls)
game/          Serveur de jeu, carte (zones de population), catalogue, vues, commandes
clinics/       Clinique, prestations et prix, personnel, matériel (achat / leasing)
inventory/     Gammes de produits et stocks (réassort automatique, CMUP)
accounting/    Plan comptable, écritures en partie double, rapports trimestriels
decisions/     Décisions en attente et leur application
engine/        Moteur : demande (pur Python) et résolution d'un trimestre
templates/     Gabarits HTML minimalistes
docs/          Document de conception (LaTeX)
```

## Tests

```bash
python manage.py test
```
