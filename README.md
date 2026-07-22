# Système de Demandes et d'Approbation

Application interne de gestion des demandes (notes de frais, congés, achats, etc.)
avec circuit d'approbation configurable, développée avec Django.

**Version actuelle :** voir [`VERSION`](VERSION).

## Fonctionnalités

- Types de demandes et formulaires configurables sans écrire de code (constructeur visuel dans l'admin)
- Moteur de règles d'approbation à plusieurs niveaux, avec résolution de conflits par spécificité
- Délégations temporaires (absences) résolues automatiquement au moment où un niveau devient actif
- Brouillons, pièces jointes, historique d'audit complet par demande
- Notifications par email sur les événements clés (soumission, décision)
- Authentification locale ou Active Directory (LDAP), au choix via configuration
- Inscription en ligne avec confirmation par email et activation par un admin fonctionnel
- Tableau de bord et rapports (volume, taux de refus, temps moyen d'approbation) avec graphiques
- Navigation et permissions de l'admin filtrées par rôle : chaque utilisateur ne voit que ce qu'il a le droit d'utiliser

## Pile technique

- **Backend :** Django 6.0.7 (Python 3.13+)
- **Admin :** [django-unfold](https://github.com/unfoldadmin/django-unfold)
- **Base de données :** SQLite en développement, SQL Server en production (`mssql-django`)
- **Fichiers statiques :** WhiteNoise
- **Serveur de production :** waitress, derrière IIS (`HttpPlatformHandler`)
- **Authentification :** `django.contrib.auth` (local) ou Active Directory (LDAP, `ldap3`)

## Démarrage rapide

Voir le [guide d'installation](INSTALL.md) pour les instructions détaillées.

```bash
python -m venv env
env\Scripts\activate          # Windows
pip install -r requirements.txt
cp .env.example .env          # puis renseigner DJANGO_SECRET_KEY
python manage.py migrate
python manage.py seed_uat     # jeu de données de démonstration (optionnel)
python manage.py runserver
```

## Tests

```bash
python manage.py test approvals
```

Voir [`TEST_RESULTS.md`](TEST_RESULTS.md) pour un dernier relevé d'exécution.

## Documentation

| Document | Contenu |
|---|---|
| [`Manuel d'Administration Fonctionnel.txt`](Manuel%20d'Administration%20Fonctionnel.txt) | Guide de l'admin fonctionnel (configuration des types de demande, règles, délégations) |
| [`Les Spécifications Techniques.txt`](Les%20Spécifications%20Techniques.txt) | Architecture, contraintes de déploiement, choix techniques |
| [`Diagrammes de Flux.txt`](Diagrammes%20de%20Flux.txt) | Circuits de soumission/approbation |
| [`Dictionnaire de Données.txt`](Dictionnaire%20de%20Données.txt) | Modèle de données |
| [`Matrice de Traçabilité.txt`](Matrice%20de%20Traçabilité.txt) | Exigences ↔ implémentation |
| [`Plan de Gestion des Risques.txt`](Plan%20de%20Gestion%20des%20Risques.txt) | Risques identifiés et mitigations |
| [`Documents de Continuité et de Reprise.txt`](Documents%20de%20Continuité%20et%20de%20Reprise.txt) | Sauvegarde et reprise après sinistre |
| [`Documents Juridiques et de Conformité.txt`](Documents%20Juridiques%20et%20de%20Conformité.txt) | Conformité et aspects juridiques |
| [`Documents d'Expérience Utilisateur.txt`](Documents%20d'Expérience%20Utilisateur.txt) | Parcours utilisateur |
| [`Documents de Connaissance et de Capitalisation.txt`](Documents%20de%20Connaissance%20et%20de%20Capitalisation.txt) | Décisions et retours d'expérience du projet |

## Configuration

Toute la configuration sensible passe par des variables d'environnement (`.env`,
jamais versionné) — voir [`.env.example`](.env.example) pour la liste complète.
Aucun secret n'est écrit en dur dans le code source.

La configuration email (SMTP) ne se fait **pas** via `.env` : elle se saisit
directement dans l'admin (« Configuration email »), pour pouvoir en changer
sans toucher au code ni redéployer.

## Licence

Propriétaire — voir [`LICENSE`](LICENSE).
