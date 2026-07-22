"""Départements/sites comme référentiels nommés (retour client) : afficher un
nom plutôt qu'un identifiant brut, et permettre à l'admin fonctionnel de
gérer ses propres groupes."""
from django.contrib.auth.models import Group, Permission, User
from django.core.management import call_command
from django.test import RequestFactory, TestCase

from .models import Department, UserProfile, Site
from .widgets import ApproversConfigBuilderWidget, CriteriaBuilderWidget


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


class ApproversConfigBuilderWidgetScopingTests(TestCase):
    """Retour client : un admin fonctionnel dont le profil a un département
    (ex: membre d'un groupe "Comité de vente" créé pour son équipe) ne doit
    voir que les utilisateurs de ce département dans le choix d'approbateur —
    un super admin, ou un admin sans département, voit tout le monde."""

    def setUp(self):
        self.factory = RequestFactory()
        self.ventes = Department.objects.create(name="Ventes")
        self.marketing = Department.objects.create(name="Marketing")
        self.alice = User.objects.create_user("alice", is_active=True)
        UserProfile.objects.create(user=self.alice, department=self.ventes)
        self.bob = User.objects.create_user("bob", is_active=True)
        UserProfile.objects.create(user=self.bob, department=self.marketing)

    def _request_as(self, user):
        request = self.factory.get("/admin/")
        request.user = user
        return request

    def test_superuser_sees_everyone(self):
        superuser = User.objects.create_superuser("root", password="x")
        widget = ApproversConfigBuilderWidget(request=self._request_as(superuser))
        html = widget.render("approvers_config", "{}")
        self.assertIn("alice", html)
        self.assertIn("bob", html)

    def test_admin_without_department_sees_everyone(self):
        admin_fonctionnel = User.objects.create_user("admin_fonctionnel", is_staff=True)
        widget = ApproversConfigBuilderWidget(request=self._request_as(admin_fonctionnel))
        html = widget.render("approvers_config", "{}")
        self.assertIn("alice", html)
        self.assertIn("bob", html)

    def test_admin_scoped_to_own_department_sees_only_that_department(self):
        scoped_admin = User.objects.create_user("scoped_admin", is_staff=True)
        UserProfile.objects.create(user=scoped_admin, department=self.ventes)
        widget = ApproversConfigBuilderWidget(request=self._request_as(scoped_admin))
        html = widget.render("approvers_config", "{}")
        self.assertIn("alice", html)
        self.assertNotIn("bob", html)

    def test_no_request_defaults_to_unrestricted(self):
        widget = ApproversConfigBuilderWidget()
        html = widget.render("approvers_config", "{}")
        self.assertIn("alice", html)
        self.assertIn("bob", html)

    def test_admin_change_page_reflects_department_scope(self):
        """Vérifie le câblage bout en bout (formfield_for_dbfield -> widget)
        sur la vraie page d'admin, pas seulement le widget isolé."""
        from django.contrib.auth.models import Permission

        from .models import ApprovalRule, RequestType

        scoped_admin = User.objects.create_user("scoped_admin2", password="x", is_staff=True)
        UserProfile.objects.create(user=scoped_admin, department=self.ventes)
        for codename in ("view_approvalrule", "change_approvalrule", "view_requesttype"):
            scoped_admin.user_permissions.add(Permission.objects.get(codename=codename))

        request_type = RequestType.objects.create(
            name="Congés", code="LEAVE", form_schema={"fields": []},
        )
        rule = ApprovalRule.objects.create(
            request_type=request_type, level=1, criteria={}, approvers_config={"type": "manager"},
        )
        self.client.force_login(scoped_admin)
        response = self.client.get(f"/admin/approvals/approvalrule/{rule.pk}/change/")
        self.assertContains(response, "alice")
        self.assertNotContains(response, "bob")


class SeedUatGroupPermissionTests(TestCase):
    """Retour client : l'admin fonctionnel doit pouvoir créer ses propres
    groupes, ce qui nécessite les permissions Django standard sur Group."""

    def test_admin_fonctionnel_can_manage_groups_after_seeding(self):
        call_command("seed_uat")

        admin_fonctionnel = User.objects.get(username="admin_fonctionnel")
        group_ct_perms = Permission.objects.filter(content_type__app_label="auth", content_type__model="group")
        for perm in group_ct_perms:
            self.assertTrue(admin_fonctionnel.has_perm(f"auth.{perm.codename}"))

    def test_group_changelist_shows_add_button_for_superuser(self):
        """Bug relevé en revue client (superuser sans bouton "Ajouter" sur
        Groupes) : django.contrib.auth enregistre Group/User avec le
        ModelAdmin Django brut, qui n'a pas l'attribut show_add_link
        qu'Unfold exige pour afficher ce bouton — indépendamment de toute
        permission. Group/User doivent être ré-enregistrés avec le
        ModelAdmin d'Unfold pour que le bouton apparaisse."""
        superuser = User.objects.create_superuser("root3", password="x")
        self.client.force_login(superuser)
        response = self.client.get("/admin/auth/group/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "addlink")
        self.assertContains(response, "/admin/auth/group/add/")

    def test_user_changelist_shows_add_button_for_superuser(self):
        superuser = User.objects.create_superuser("root4", password="x")
        self.client.force_login(superuser)
        response = self.client.get("/admin/auth/user/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "addlink")

    def test_seed_uat_group_permission_grant_reflected_on_group_object(self):
        call_command("seed_uat")
        group_ct_perms = Permission.objects.filter(content_type__app_label="auth", content_type__model="group")
        group = Group.objects.get(name="Admins fonctionnels")
        for perm in group_ct_perms:
            self.assertIn(perm, group.permissions.all())

    def test_admin_fonctionnel_can_reach_group_add_page(self):
        """Vérification bout en bout du retour client "je ne peux pas créer
        de groupe" : la page d'ajout doit être accessible avec les permissions
        posées par seed_uat, pas seulement les permissions en base."""
        call_command("seed_uat")
        self.client.login(username="admin_fonctionnel", password="AdminFonc!2026")
        response = self.client.get("/admin/auth/group/add/")
        self.assertEqual(response.status_code, 200)


class AdminDashboardTests(TestCase):
    """Retour client : un tableau de bord pour l'administrateur, comme celui
    des utilisateurs, avec des graphiques — et les nouveaux modèles
    (Department, Site) doivent apparaître dans la navigation de l'admin."""

    def test_dashboard_renders_stats_and_charts_for_superuser(self):
        from django.utils import timezone

        from .models import Request, RequestType

        superuser = User.objects.create_superuser("root", password="x")
        request_type = RequestType.objects.create(name="Congés", code="LEAVE", form_schema={"fields": []})
        now = timezone.now()
        Request.objects.create(
            request_type=request_type, requester=superuser, status=Request.Status.APPROVED,
            submitted_at=now, completed_at=now,
        )
        self.client.force_login(superuser)
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Vue d'ensemble")
        self.assertContains(response, "Accès rapide")
        self.assertContains(response, 'data-type="bar"')
        self.assertContains(response, "Tableau de bord")

    def test_dashboard_reachable_for_staff_admin(self):
        staff = User.objects.create_user("staff1", password="x", is_staff=True)
        self.client.login(username="staff1", password="x")
        response = self.client.get("/admin/")
        self.assertEqual(response.status_code, 200)

    def test_department_and_site_admin_pages_reachable(self):
        superuser = User.objects.create_superuser("root2", password="x")
        self.client.force_login(superuser)
        self.assertEqual(self.client.get("/admin/approvals/department/").status_code, 200)
        self.assertEqual(self.client.get("/admin/approvals/site/").status_code, 200)
