"""Départements/sites comme référentiels nommés (retour client) : afficher un
nom plutôt qu'un identifiant brut, et permettre à l'admin fonctionnel de
gérer ses propres groupes."""
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import TestCase

from .models import Department, Site
from .widgets import CriteriaBuilderWidget


class DepartmentSiteModelTests(TestCase):
    def test_str_returns_name(self):
        department = Department.objects.create(name="Marketing")
        site = Site.objects.create(name="Lyon")
        self.assertEqual(str(department), "Marketing")
        self.assertEqual(str(site), "Lyon")


class CriteriaBuilderWidgetTests(TestCase):
    def test_render_offers_department_and_site_names(self):
        Department.objects.create(name="Ventes")
        Site.objects.create(name="Paris")
        html = CriteriaBuilderWidget().render("criteria", "{}")
        self.assertIn("Ventes", html)
        self.assertIn("Paris", html)


class SeedUatGroupPermissionTests(TestCase):
    """Retour client : l'admin fonctionnel doit pouvoir créer ses propres
    groupes, ce qui nécessite les permissions Django standard sur Group."""

    def test_admin_fonctionnel_can_manage_groups_after_seeding(self):
        call_command("seed_uat")

        admin_fonctionnel = User.objects.get(username="admin_fonctionnel")
        group_ct_perms = Permission.objects.filter(content_type__app_label="auth", content_type__model="group")
        for perm in group_ct_perms:
            self.assertTrue(admin_fonctionnel.has_perm(f"auth.{perm.codename}"))

        group = Group.objects.get(name="Admins fonctionnels")
        for perm in group_ct_perms:
            self.assertIn(perm, group.permissions.all())
