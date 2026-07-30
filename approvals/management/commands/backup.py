"""Sauvegarde de la base de données et des fichiers utilisateur (media) —
retour client : "si l'équipement ou un serveur tombe en panne, on ne pourra
rien récupérer". Voir "Guide de Sauvegarde et Restauration.md" pour la
procédure complète (planification automatique, transfert hors serveur).

SQLite uniquement (déploiement actuel) : utilise l'API .backup() native de
SQLite plutôt qu'une copie de fichier — cohérent même si la base est en
cours d'écriture, contrairement à une copie brute qui pourrait capturer un
fichier à moitié écrit. Fonctionne aussi bien sur une base réelle qu'en
mémoire (utile pour les tests). Pour SQL Server, cette commande ne
sauvegarde que les fichiers media : la base doit être sauvegardée avec les
outils natifs de SQL Server (BACKUP DATABASE), gérés par la DSI.
"""
import shutil
import sqlite3
import tarfile
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import connection
from django.utils import timezone


class Command(BaseCommand):
    help = (
        "Sauvegarde la base de données (SQLite) et les fichiers utilisateur (media) dans "
        "BACKUP_ROOT, avec purge automatique au-delà de BACKUP_RETENTION_COUNT sauvegardes."
    )

    def add_arguments(self, parser):
        parser.add_argument(
            "--retention", type=int, default=None,
            help="Nombre de sauvegardes à conserver (par défaut : réglage BACKUP_RETENTION_COUNT).",
        )

    def handle(self, *args, **options):
        backup_root = Path(settings.BACKUP_ROOT)
        backup_root.mkdir(parents=True, exist_ok=True)
        # Précision à la microseconde (pas juste la seconde) : deux
        # sauvegardes lancées dans la même seconde entreraient sinon en
        # collision sur le même nom de dossier — ça arrive en pratique, ex:
        # `restore` déclenche sa propre sauvegarde de sécurité juste avant
        # de restaurer, souvent moins d'une seconde après une sauvegarde
        # manuelle précédente.
        stamp = timezone.now().strftime("%Y%m%d_%H%M%S%f")
        target_dir = backup_root / stamp
        target_dir.mkdir()

        if connection.vendor == "sqlite":
            self._backup_sqlite(target_dir)
        else:
            self.stdout.write(self.style.WARNING(
                f"Moteur de base '{connection.vendor}' détecté : cette commande ne sauvegarde "
                "que SQLite. Utilise les outils de sauvegarde natifs (SQL Server BACKUP DATABASE) "
                "pour la base de données — voir Guide de Sauvegarde et Restauration.md."
            ))

        self._backup_media(target_dir)
        self._prune_old_backups(backup_root, options.get("retention"))
        self.stdout.write(self.style.SUCCESS(f"Sauvegarde terminée : {target_dir}"))

    def _backup_sqlite(self, target_dir):
        dest_path = target_dir / "db.sqlite3"
        connection.ensure_connection()
        dest_conn = sqlite3.connect(dest_path)
        with dest_conn:
            connection.connection.backup(dest_conn)
        dest_conn.close()

    def _backup_media(self, target_dir):
        media_root = Path(settings.MEDIA_ROOT)
        if not media_root.exists() or not any(media_root.iterdir()):
            return
        archive_path = target_dir / "media.tar.gz"
        with tarfile.open(archive_path, "w:gz") as tar:
            tar.add(media_root, arcname="media")

    def _prune_old_backups(self, backup_root, retention):
        retention = retention if retention is not None else settings.BACKUP_RETENTION_COUNT
        backups = sorted((p for p in backup_root.iterdir() if p.is_dir()), reverse=True)
        removed = 0
        for old in backups[retention:]:
            shutil.rmtree(old)
            removed += 1
        if removed:
            self.stdout.write(f"{removed} ancienne(s) sauvegarde(s) supprimée(s) (rétention : {retention}).")
