# Résultats des tests

**Dernière exécution :** 2026-07-22
**Commande :** `python manage.py test approvals`
**Résultat :** 116 / 116 tests réussis (0 échec, 0 erreur)

## Répartition par module

| Module | Objet | Tests |
|---|---|---|
| `approvals/tests.py` | Moteur de routage (`WorkflowEngine`) : approbation multi-niveaux, délégations, conflits de règles, interventions admin, rapports | 24 |
| `approvals/test_views.py` | Bout en bout via le client HTTP : brouillons, pièces jointes, pagination, profil, photo, notifications, style de connexion | 41 |
| `approvals/test_registration.py` | Inscription en ligne, confirmation par email, double authentification à la connexion, chiffrement de la config SMTP | 15 |
| `approvals/test_departments.py` | Départements/Sites, permissions de navigation admin, tableau de bord admin | 18 |
| `approvals/test_ldap.py` | Authentification Active Directory (simulée, aucun annuaire réel disponible pour ce poste de développement) | 8 |
| `approvals/test_notifications.py` | Notifications email sur les événements métier (soumission, décision) | 7 |
| `approvals/test_forms.py` | Devise configurable des montants | 3 |
| **Total** | | **116** |

## Ce qui n'est pas couvert par ces tests

Trois points nécessitent une infrastructure que ce poste de développement n'a
pas (voir Spécifications Techniques §10 et le Manuel d'Administration
Fonctionnel, section « Déploiement entreprise ») :

- **SQL Server** — le code est écrit pour basculer automatiquement dès que
  `SQLSERVER_HOST` est renseigné, mais n'a pas pu être testé contre un
  serveur réel.
- **Active Directory** — le backend LDAP est testé par simulation
  (`approvals/test_ldap.py`), pas contre un annuaire réel.
- **IIS** — le déploiement via `HttpPlatformHandler` (`web.config`,
  `run_production.py`) n'a pas pu être vérifié en conditions réelles.

## Reproduire ces résultats

```bash
python manage.py test approvals -v 2
```

L'option `-v 2` affiche le nom de chaque test au fur et à mesure de son
exécution.
