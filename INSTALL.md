# Guide d'installation

## Prérequis

- Python 3.13 ou supérieur
- Aucun droit administrateur local requis (tous les paquets sont des roues Python pures ou précompilées — voir les Spécifications Techniques §1.2)
- Pour la production : IIS avec le module `HttpPlatformHandler`, et un serveur SQL Server si vous n'utilisez pas SQLite

## 1. Installation locale (développement)

```bash
git clone <url-du-dépôt>
cd demandes_approbation_systems

python -m venv env
env\Scripts\activate            # Windows (PowerShell/cmd)
# source env/bin/activate       # macOS/Linux

pip install -r requirements.txt
```

## 2. Configuration

```bash
cp .env.example .env
```

Ouvrez `.env` et renseignez au minimum `DJANGO_SECRET_KEY` :

```bash
python -c "from django.core.management.utils import get_random_secret_key; print(get_random_secret_key())"
```

Les autres variables sont optionnelles en développement local (voir les
commentaires dans `.env.example`) :
- `DJANGO_ALLOWED_HOSTS` — hôtes autorisés (obligatoire dès que `DJANGO_DEBUG=False`)
- `AD_LDAP_*` — laissez vide pour désactiver l'authentification Active Directory (authentification locale utilisée à la place)
- `SQLSERVER_*` — laissez `SQLSERVER_HOST` vide pour utiliser SQLite localement

## 3. Base de données

```bash
python manage.py migrate
```

Pour un jeu de données de démonstration (comptes de test, types de demande,
règles) :

```bash
python manage.py seed_uat
```

Cette commande peut être relancée à tout moment sans dupliquer les données.
Les comptes créés (identifiants, rôles) sont listés dans le Manuel
d'Administration Fonctionnel.

## 4. Lancer le serveur de développement

```bash
python manage.py runserver
```

L'application est accessible sur http://127.0.0.1:8000/, l'administration sur
http://127.0.0.1:8000/admin/.

## 5. Lancer les tests

```bash
python manage.py test approvals
```

## 6. Déploiement en production (IIS)

Le déploiement est pris en charge par la DSI, pas par l'application elle-même
(Spécifications Techniques §2.2). Résumé des étapes :

1. Copier le code sur le serveur, créer l'environnement virtuel (`env/`) et
   installer les dépendances (`pip install -r requirements.txt`).
2. Configurer les variables d'environnement dans `web.config`
   (`DJANGO_SECRET_KEY`, `AD_LDAP_BIND_PASSWORD`, `SQLSERVER_PASSWORD`, etc.
   — jamais en dur dans le fichier versionné, voir les commentaires de
   `web.config`).
3. `DJANGO_DEBUG=False` et `DJANGO_ALLOWED_HOSTS` renseigné avec le nom
   d'hôte réel.
4. Exécuter les migrations une seule fois avant bascule du trafic :
   `python manage.py migrate`
5. Collecter les fichiers statiques : `python manage.py collectstatic`
   (WhiteNoise sert aussi les fichiers directement depuis les apps en
   secours si cette étape est oubliée, mais elle reste recommandée en
   production).
6. Créer un compte super-administrateur : `python manage.py createsuperuser`
7. Configurer le pool d'applications IIS avec `HttpPlatformHandler` pointant
   vers `run_production.py` (voir `web.config`, section commentée).
8. Si le trafic HTTPS est terminé par IIS, définir `DJANGO_FORCE_HTTPS=True`
   une fois IIS configuré pour transmettre l'en-tête `X-Forwarded-Proto`
   (sinon cela provoque une boucle de redirection).

Pour l'authentification Active Directory et SQL Server, voir respectivement
`approvals/auth_backends.py` et la section « Database » de
`demand_approb_system_main/settings.py`.
