"""Restaure une sauvegarde précédente (base + media) — ACTION DESTRUCTIVE,
écrase les données actuelles. Voir "Guide de Sauvegarde et Restauration.md".

Arrêter le service applicatif avant de restaurer (`sudo systemctl stop
demande_approbation.service`) : restaurer une base pendant que l'app reçoit
des requêtes peut mener à un état incohérent entre la base restaurée et les
connexions déjà ouvertes.
"""
import shutil
import sqlite3
import tarfile
from pathlib import Path

from django.conf import settings
from django.core.management import call_command
from django.core.management.base import BaseCommand, CommandError
from django.db import connection


class Command(BaseCommand):
    help = "Restaure une sauvegarde précédente (base + media). ACTION DESTRUCTIVE."

    def add_arguments(self, parser):
        parser.add_argument(
            "backup_name",
            help="Nom du dossier de sauvegarde à restaurer (ex: 20260801_030000), voir BACKUP_ROOT.",
        )
        parser.add_argument(
            "--yes", action="store_true",
            help="Confirme l'action (obligatoire — écrase la base de données et les fichiers actuels).",
        )
        parser.add_argument(
            "--skip-safety-backup", action="store_true",
            help="Ne pas sauvegarder l'état actuel avant de restaurer (déconseillé).",
        )

    def handle(self, *args, **options):
        if not options["yes"]:
            raise CommandError(
                "Action destructive : relance avec --yes pour confirmer explicitement "
                "(écrase la base de données et les fichiers utilisateur actuels)."
            )

        backup_root = Path(settings.BACKUP_ROOT)
        source_dir = backup_root / options["backup_name"]
        if not source_dir.is_dir():
            raise CommandError(f"Sauvegarde introuvable : {source_dir}")

        if not options["skip_safety_backup"]:
            self.stdout.write("Sauvegarde de sécurité de l'état actuel avant restauration...")
            call_command("backup")

        db_backup = source_dir / "db.sqlite3"
        if db_backup.exists():
            if connection.vendor == "sqlite":
                self._restore_sqlite(db_backup)
                self.stdout.write(self.style.SUCCESS(f"Base de données restaurée depuis {db_backup}."))
            else:
                self.stdout.write(self.style.WARNING(
                    f"Moteur de base '{connection.vendor}' détecté : cette commande ne restaure "
                    "que SQLite. Restaure la base via les outils natifs de SQL Server."
                ))

        media_archive = source_dir / "media.tar.gz"
        if media_archive.exists():
            self._restore_media(media_archive)
            self.stdout.write(self.style.SUCCESS(f"Fichiers utilisateur restaurés depuis {media_archive}."))

        self.stdout.write(self.style.SUCCESS("Restauration terminée."))

    def _restore_sqlite(self, db_backup):
        source_conn = sqlite3.connect(db_backup)
        connection.ensure_connection()
        with connection.connection:
            source_conn.backup(connection.connection)
        source_conn.close()

    def _restore_media(self, media_archive):
        media_root = Path(settings.MEDIA_ROOT)
        if media_root.exists():
            shutil.rmtree(media_root)
        with tarfile.open(media_archive, "r:gz") as tar:
            tar.extractall(media_root.parent, filter="data")
