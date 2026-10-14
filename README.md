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

Les décisions des joueurs se saisissent pour l'instant via l'admin
(`/admin/decisions/pendingdecision/`) ; la carte et les tableaux de bord sont
consultables sur `/`.

## Stress tests

Chaque serveur accepte des surcharges de paramètres (voir
`engine/parameters.py`) :

```bash
python manage.py seed_demo --name "Choc de demande" --param demand_multiplier=0.7
python manage.py seed_demo --name "Inflation fournisseurs" --param purchase_cost_multiplier=1.25
python manage.py seed_demo --name "Charges sociales +" --param employer_contribution_rate=0.50
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
