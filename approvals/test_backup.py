"""Sauvegarde/restauration (manage.py backup/restore) — retour client :
"si l'équipement ou un serveur tombe en panne, on ne pourra rien récupérer".

TransactionTestCase (pas TestCase) : ces commandes manipulent la connexion
SQLite directement via l'API .backup() — TestCase enveloppe chaque test
dans une transaction non validée, ce qui n'est pas représentatif d'un
usage réel de cette API et peut se comporter différemment.
"""
import shutil
import tempfile
from pathlib import Path

from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import TransactionTestCase, override_settings

from .models import Department


class BackupCommandTests(TransactionTestCase):
    def setUp(self):
        self.backup_root = Path(tempfile.mkdtemp())
        self.media_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.backup_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

    def test_backup_creates_a_timestamped_sqlite_snapshot(self):
        Department.objects.create(name="Ventes")
        with override_settings(BACKUP_ROOT=str(self.backup_root), MEDIA_ROOT=str(self.media_root)):
            call_command("backup")

        backups = list(self.backup_root.iterdir())
        self.assertEqual(len(backups), 1)
        self.assertTrue((backups[0] / "db.sqlite3").exists())

    def test_backup_skips_media_archive_when_no_files(self):
        with override_settings(BACKUP_ROOT=str(self.backup_root), MEDIA_ROOT=str(self.media_root)):
            call_command("backup")
        backup_dir = next(self.backup_root.iterdir())
        self.assertFalse((backup_dir / "media.tar.gz").exists())

    def test_backup_includes_media_archive_when_files_present(self):
        (self.media_root / "profile_photos").mkdir()
        (self.media_root / "profile_photos" / "test.jpg").write_bytes(b"fake-image-content")

        with override_settings(BACKUP_ROOT=str(self.backup_root), MEDIA_ROOT=str(self.media_root)):
            call_command("backup")

        backup_dir = next(self.backup_root.iterdir())
        self.assertTrue((backup_dir / "media.tar.gz").exists())

    def test_backup_prunes_old_backups_beyond_retention(self):
        for name in ("20260101_000000", "20260102_000000", "20260103_000000"):
            (self.backup_root / name).mkdir()

        with override_settings(BACKUP_ROOT=str(self.backup_root), MEDIA_ROOT=str(self.media_root)):
            call_command("backup", "--retention", "2")

        remaining = sorted(p.name for p in self.backup_root.iterdir())
        # Les 3 anciens dossiers factices + le nouveau créé par la commande
        # elle-même = 4 avant purge ; la rétention=2 ne garde que les 2 plus récents.
        self.assertEqual(len(remaining), 2)
        self.assertIn("20260103_000000", remaining)


class RestoreCommandTests(TransactionTestCase):
    def setUp(self):
        self.backup_root = Path(tempfile.mkdtemp())
        self.media_root = Path(tempfile.mkdtemp())
        self.addCleanup(shutil.rmtree, self.backup_root, ignore_errors=True)
        self.addCleanup(shutil.rmtree, self.media_root, ignore_errors=True)

    def test_restore_without_yes_flag_is_refused(self):
        with override_settings(BACKUP_ROOT=str(self.backup_root), MEDIA_ROOT=str(self.media_root)):
            with self.assertRaises(CommandError):
                call_command("restore", "some_backup")

    def test_restore_rejects_unknown_backup_name(self):
        with override_settings(BACKUP_ROOT=str(self.backup_root), MEDIA_ROOT=str(self.media_root)):
            with self.assertRaises(CommandError):
                call_command("restore", "does_not_exist", "--yes")

    def test_backup_then_restore_round_trip(self):
        """Le scénario réel : des données existent, on les sauvegarde, elles
        sont ensuite perdues/modifiées, puis restaurées à l'identique."""
        Department.objects.create(name="Ventes")

        with override_settings(BACKUP_ROOT=str(self.backup_root), MEDIA_ROOT=str(self.media_root)):
            call_command("backup")
            backup_name = next(self.backup_root.iterdir()).name

            # Simule une perte/altération de données après la sauvegarde.
            Department.objects.all().delete()
            Department.objects.create(name="Donnée corrompue")
            self.assertEqual(Department.objects.count(), 1)
            self.assertEqual(Department.objects.first().name, "Donnée corrompue")

            call_command("restore", backup_name, "--yes", "--skip-safety-backup")

        self.assertEqual(Department.objects.count(), 1)
        self.assertEqual(Department.objects.first().name, "Ventes")

    def test_restore_creates_safety_backup_of_current_state_first(self):
        Department.objects.create(name="Avant restauration")
        with override_settings(BACKUP_ROOT=str(self.backup_root), MEDIA_ROOT=str(self.media_root)):
            call_command("backup")
            backup_name = next(self.backup_root.iterdir()).name

            call_command("restore", backup_name, "--yes")

        # La commande a dû créer une 2e sauvegarde automatiquement (l'état
        # juste avant la restauration), en plus de celle déjà présente.
        self.assertEqual(len(list(self.backup_root.iterdir())), 2)
