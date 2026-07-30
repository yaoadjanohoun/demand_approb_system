# Guide de Sauvegarde et Restauration

Retour client : *"si l'équipement ou un serveur tombe en panne, on ne pourra
rien récupérer"*. Ce document couvre le mécanisme mis en place pour éviter
ça — sauvegarde automatique quotidienne de la base de données et des
fichiers utilisateur (photos de profil, pièces jointes), et procédure de
restauration testée.

## ⚠️ Point important à régler avec la DSI

Par défaut, les sauvegardes sont écrites **sur le même serveur** que
l'application (`BACKUP_ROOT`, voir `.env.example`). C'est déjà utile contre
une suppression accidentelle ou une corruption de données, **mais ça ne
protège pas contre une panne matérielle complète du serveur** — si la
machine tombe en panne, les sauvegardes tombent avec elle.

**Il faut prévoir un espace de stockage HORS de ce serveur** (partage
réseau, autre serveur, stockage cloud fourni par la DSI) vers lequel copier
régulièrement le contenu de `BACKUP_ROOT`. Deux façons de faire une fois cet
espace disponible :
- Monter cet espace directement sur le serveur et faire pointer `BACKUP_ROOT`
  dessus (le plus simple — les sauvegardes s'y écrivent directement).
- Ou garder `BACKUP_ROOT` local et ajouter une synchronisation régulière
  (`rsync`, tâche planifiée) vers l'espace externe.

Tant que ce point n'est pas réglé, les sauvegardes locales restent un
filet de sécurité partiel, pas une vraie protection contre un sinistre.

## Ce qui est sauvegardé

- **La base de données** (SQLite en production actuellement) — via l'API de
  sauvegarde native de SQLite (`.backup()`), pas une simple copie de
  fichier : reste cohérente même si l'application écrit dedans au même
  moment.
- **Les fichiers utilisateur** (`media/` : photos de profil, pièces
  jointes) — dans une archive `.tar.gz`, uniquement si des fichiers existent.

Si le projet passe un jour sur SQL Server (prévu dans les spécifications
techniques mais pas utilisé actuellement), `manage.py backup` ne sauvegarde
plus la base — un message l'indique clairement. La sauvegarde de SQL Server
doit alors passer par les outils natifs (`BACKUP DATABASE`), gérés par la
DSI, pas par cette commande.

## Sauvegarde manuelle

```bash
cd ~/dockers/Demande_Approbation
python manage.py backup
```

Crée un dossier horodaté dans `BACKUP_ROOT` (par défaut `backups/` à la
racine du projet), par exemple `backups/20260801_030000/`, contenant
`db.sqlite3` et, s'il y a des fichiers, `media.tar.gz`.

Les sauvegardes de plus de `BACKUP_RETENTION_COUNT` (14 par défaut, voir
`.env.example`) sont automatiquement supprimées à chaque exécution — les
plus récentes sont toujours conservées.

## Sauvegarde automatique (planification quotidienne)

Le dossier `systemd/` du dépôt contient les fichiers nécessaires
(`demande-approbation-backup.service` et `.timer`) — adapter les chemins et
l'utilisateur aux valeurs réelles du serveur avant de les installer :

```bash
sudo cp systemd/demande-approbation-backup.service /etc/systemd/system/
sudo cp systemd/demande-approbation-backup.timer /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now demande-approbation-backup.timer
```

Vérifier que la planification est bien active :

```bash
systemctl list-timers demande-approbation-backup.timer
```

Déclencher une sauvegarde immédiatement, sans attendre l'heure planifiée
(utile pour vérifier que tout fonctionne après l'installation) :

```bash
sudo systemctl start demande-approbation-backup.service
journalctl -u demande-approbation-backup.service --no-pager -n 20
```

## Restauration

**Action destructive** : écrase la base de données et les fichiers
utilisateur actuels. Toujours vérifier le nom exact de la sauvegarde avant
de lancer la commande.

```bash
cd ~/dockers/Demande_Approbation
sudo systemctl stop demande_approbation.service   # évite un état incohérent pendant la restauration
ls backups/                                        # repérer le nom exact du dossier à restaurer
python manage.py restore 20260801_030000 --yes
sudo systemctl start demande_approbation.service
```

Par sécurité, `restore` sauvegarde automatiquement l'état actuel juste
avant d'écraser quoi que ce soit (désactivable avec `--skip-safety-backup`,
déconseillé) — en cas d'erreur sur le nom de sauvegarde choisi, l'état
d'avant la restauration reste récupérable.

## Tester la restauration périodiquement

Une sauvegarde qui n'a jamais été restaurée avec succès n'est pas une
garantie. À faire de temps en temps (ex: trimestriellement, voir
`Documents de Continuité et de Reprise.txt`) :

1. Sur un environnement de test (jamais en production), lancer
   `python manage.py restore <nom> --yes`.
2. Vérifier que l'application démarre normalement et que les données
   restaurées sont cohérentes (se connecter, consulter quelques demandes).
3. Noter le résultat du test (date, succès/échec, actions correctives si
   besoin) — voir le gabarit de REX dans
   `Documents de Connaissance et de Capitalisation.txt`.
