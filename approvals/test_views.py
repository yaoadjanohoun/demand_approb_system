"""Tests de bout en bout via le client de test Django (requêtes HTTP réelles).

Ajouté après un bug en revue client : un approbateur intermédiaire recevait un
403 Forbidden juste après avoir approuvé un niveau, parce que la vue le
redirigeait vers le détail de la demande alors qu'il n'était plus autorisé à
la consulter (elle était passée au niveau suivant). Les tests sur
WorkflowEngine seul ne pouvaient pas détecter ça : le moteur fonctionnait
très bien, c'est l'enchaînement décision -> redirection -> permission de vue
qui était cassé.
"""
import datetime
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings
from django.utils import timezone

from .models import ApprovalRule, Request, RequestAttachment, RequestType, UserProfile


class DecisionConfirmationTests(TestCase):
    """Retour client : les boutons Approuver/Retourner/Refuser sont côte à
    côte, faciles à confondre en clic rapide — Retourner/Refuser doivent
    demander confirmation (via la modale déjà utilisée ailleurs dans l'app),
    pas Approuver (action positive, sans risque de "mégarde" à corriger)."""

    def setUp(self):
        self.manager = User.objects.create_user("manager1", password="x")
        self.employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=self.employee, manager=self.manager)
        self.request_type = RequestType.objects.create(
            name="Congés", code="LEAVE", form_schema={"fields": []},
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"},
        )
        self.req = Request.objects.create(request_type=self.request_type, requester=self.employee)
        from .services import WorkflowEngine

        WorkflowEngine(self.req).submit()
        self.client.login(username="manager1", password="x")

    def test_reject_and_return_forms_ask_for_confirmation_not_approve(self):
        response = self.client.get(f"/{self.req.pk}/")
        self.assertContains(response, 'id="reject-form"')
        self.assertContains(response, "Confirmez-vous le refus")
        self.assertContains(response, 'id="return-form"')
        self.assertContains(response, "Confirmes-tu le retour")
        self.assertNotContains(response, 'id="approve-form" data-confirm')


class ApproverCanStillViewAfterActingTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("manager1", password="x")
        self.director = User.objects.create_user("director1", password="x")
        self.employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=self.employee, manager=self.manager)

        self.request_type = RequestType.objects.create(
            name="Note de frais", code="EXPENSE",
            form_schema={"fields": [{"name": "montant", "type": "decimal", "required": True}]},
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"}
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=2, criteria={"min_amount": 1000},
            approvers_config={"type": "user", "user_id": self.director.id},
        )

        self.client = Client()

    def submit_via_http(self, montant):
        self.client.login(username="employee1", password="x")
        response = self.client.post(
            f"/new/{self.request_type.id}/", {"montant": montant, "motif": "test"}
        )
        self.assertEqual(response.status_code, 302)
        request_id = response.url.strip("/").rsplit("/", 1)[-1]
        self.client.logout()
        return Request.objects.get(pk=request_id)

    def test_manager_can_view_request_after_approving_it_to_next_level(self):
        req = self.submit_via_http(1500)

        self.client.login(username="manager1", password="x")
        approve_response = self.client.post(f"/{req.pk}/approve/", follow=True)

        self.assertEqual(approve_response.status_code, 200)
        self.assertNotContains(approve_response, "403", status_code=200)
        req.refresh_from_db()
        self.assertEqual(req.current_level, 2)
        self.assertEqual(req.status, Request.Status.PENDING)

        # Revisiter la page directement (comme le ferait un rafraîchissement) : pas de 403.
        detail_response = self.client.get(f"/{req.pk}/")
        self.assertEqual(detail_response.status_code, 200)

    def test_manager_can_view_after_final_approval_too(self):
        req = self.submit_via_http(500)  # un seul niveau (montant < 1000)

        self.client.login(username="manager1", password="x")
        response = self.client.post(f"/{req.pk}/approve/", follow=True)

        self.assertEqual(response.status_code, 200)
        req.refresh_from_db()
        self.assertEqual(req.status, Request.Status.APPROVED)

    def test_director_can_view_request_routed_to_them_even_after_delegation(self):
        from datetime import timedelta

        from django.utils import timezone

        from .models import Delegation

        delegate = User.objects.create_user("director1_delegate", password="x")
        Delegation.objects.create(
            delegator=self.director, delegate=delegate,
            start_date=timezone.localdate() - timedelta(days=1),
            end_date=timezone.localdate() + timedelta(days=1),
        )
        req = self.submit_via_http(1500)
        self.client.login(username="manager1", password="x")
        self.client.post(f"/{req.pk}/approve/")

        self.client.logout()
        self.client.login(username="director1", password="x")
        response = self.client.get(f"/{req.pk}/")
        self.assertEqual(response.status_code, 200)


class ProfilePageTests(TestCase):
    """Bug en revue client : /profil/ renvoyait un 500 pour tout utilisateur
    sans manager assigné (director1, director1_delegate, admin dans le jeu de
    données UAT). Cause : {{ profile.manager.get_full_name|default:profile.manager.username }}
    dans le template -- accéder à un attribut d'un objet None DANS L'ARGUMENT
    D'UN FILTRE lève VariableDoesNotExist au lieu de s'effacer silencieusement
    (contrairement à {{ profile.manager.username }} seul, qui s'affiche vide).
    """

    def test_profile_page_works_without_manager(self):
        user = User.objects.create_user("no_manager_user", password="x")
        self.client.login(username="no_manager_user", password="x")
        response = self.client.get("/profil/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "no_manager_user")

    def test_profile_page_works_with_manager(self):
        manager = User.objects.create_user("manager1", password="x", first_name="Marc", last_name="Manager")
        employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=employee, manager=manager)
        self.client.login(username="employee1", password="x")
        response = self.client.get("/profil/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Marc Manager")

    def test_profile_page_creates_profile_if_missing(self):
        User.objects.create_user("brand_new_user", password="x")
        self.client.login(username="brand_new_user", password="x")
        response = self.client.get("/profil/")
        self.assertEqual(response.status_code, 200)
        self.assertTrue(UserProfile.objects.filter(user__username="brand_new_user").exists())

    def test_profile_page_shows_department_and_site_names_not_raw_ids(self):
        """Retour client : ne pas afficher les identifiants des départements/sites,
        mais leurs noms."""
        from .models import Department, Site

        department = Department.objects.create(name="Ventes")
        site = Site.objects.create(name="Lyon")
        user = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=user, department=department, site=site)
        self.client.login(username="employee1", password="x")

        response = self.client.get("/profil/")
        self.assertContains(response, "Ventes")
        self.assertContains(response, "Lyon")
        self.assertNotContains(response, f">{department.id}<")


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ProfilePhotoTests(TestCase):
    """Photo de profil (retour client) : stockée sur disque via ImageField,
    la base ne garde qu'un chemin de fichier — jamais le binaire de l'image."""

    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def _tiny_png(self, name="avatar.png"):
        import io

        from django.core.files.uploadedfile import SimpleUploadedFile
        from PIL import Image

        buffer = io.BytesIO()
        Image.new("RGB", (1, 1), color="white").save(buffer, format="PNG")
        return SimpleUploadedFile(name, buffer.getvalue(), content_type="image/png")

    def test_uploading_photo_stores_file_path_not_binary_in_db(self):
        user = User.objects.create_user("photo_user", password="x")
        self.client.login(username="photo_user", password="x")

        response = self.client.post(
            "/profil/", {"action": "update_photo", "photo": self._tiny_png()}, follow=True
        )
        self.assertEqual(response.status_code, 200)

        profile = UserProfile.objects.get(user=user)
        self.assertTrue(profile.photo.name.startswith("profile_photos/"))
        profile.photo.delete(save=False)

    def test_removing_photo_clears_the_field(self):
        user = User.objects.create_user("photo_user2", password="x")
        profile = UserProfile.objects.create(user=user)
        profile.photo.save("avatar.png", self._tiny_png(), save=True)
        self.client.login(username="photo_user2", password="x")

        response = self.client.post("/profil/", {"action": "remove_photo"}, follow=True)
        self.assertEqual(response.status_code, 200)

        profile.refresh_from_db()
        self.assertFalse(profile.photo)

    def test_oversized_photo_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = User.objects.create_user("photo_user3", password="x")
        self.client.login(username="photo_user3", password="x")
        big_file = SimpleUploadedFile(
            "big.png", b"\x89PNG\r\n\x1a\n" + b"0" * (3 * 1024 * 1024), content_type="image/png"
        )
        response = self.client.post("/profil/", {"action": "update_photo", "photo": big_file})
        self.assertEqual(response.status_code, 200)  # ré-affiche le formulaire avec l'erreur
        self.assertFalse(UserProfile.objects.get(user=user).photo)

    def test_wrong_file_type_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = User.objects.create_user("photo_user4", password="x")
        self.client.login(username="photo_user4", password="x")
        bad_file = SimpleUploadedFile("notes.txt", b"pas une image", content_type="text/plain")
        response = self.client.post("/profil/", {"action": "update_photo", "photo": bad_file})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserProfile.objects.get(user=user).photo)


class PersonalInfoEditTests(TestCase):
    """Tout utilisateur doit pouvoir modifier lui-même son nom d'utilisateur,
    son nom complet et son email (retour client) — le reste (manager,
    département, site) reste réservé à un admin fonctionnel."""

    def setUp(self):
        self.user = User.objects.create_user(
            "employee1", password="x", email="old@example.com", first_name="Old", last_name="Name",
        )
        self.client.login(username="employee1", password="x")

    def test_updating_personal_info_saves_changes(self):
        response = self.client.post("/profil/", {
            "action": "update_info",
            "username": "employee1_renamed",
            "first_name": "New",
            "last_name": "Name",
            "email": "new@example.com",
        })
        self.assertEqual(response.status_code, 302)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "employee1_renamed")
        self.assertEqual(self.user.first_name, "New")
        self.assertEqual(self.user.email, "new@example.com")

    def test_duplicate_username_rejected(self):
        User.objects.create_user("taken_username", password="x")
        response = self.client.post("/profil/", {
            "action": "update_info",
            "username": "taken_username",
            "first_name": "Old", "last_name": "Name", "email": "old@example.com",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.username, "employee1")

    def test_first_name_with_stray_characters_rejected(self):
        """Retour déploiement : un "Prénom" enregistré avec une suite de "/"
        (aucune validation avant) cassait l'affichage du nom complet partout
        dans l'app (listes, emails, historique d'audit)."""
        response = self.client.post("/profil/", {
            "action": "update_info",
            "username": "employee1", "first_name": "Super////////", "last_name": "Name",
            "email": "old@example.com",
        })
        self.assertEqual(response.status_code, 200)
        self.user.refresh_from_db()
        self.assertEqual(self.user.first_name, "Old")

    def test_manager_department_site_not_editable_via_this_form(self):
        from .models import Department

        manager = User.objects.create_user("manager1", password="x")
        department = Department.objects.create(name="Ventes")
        UserProfile.objects.create(user=self.user, manager=manager, department=department)
        response = self.client.post("/profil/", {
            "action": "update_info",
            "username": "employee1", "first_name": "Old", "last_name": "Name", "email": "old@example.com",
            "manager": "", "department": "999",
        })
        self.assertEqual(response.status_code, 302)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.manager_id, manager.id)
        self.assertEqual(profile.department_id, department.id)


class DraftRequestTests(TestCase):
    """Flux brouillon (retour client) : une demande peut être enregistrée
    incomplète, reprise plus tard, puis soumise — et seul un brouillon peut
    être supprimé (une demande déjà soumise doit rester dans l'historique)."""

    def setUp(self):
        self.employee = User.objects.create_user("employee1", password="x")
        self.other_user = User.objects.create_user("employee2", password="x")
        self.request_type = RequestType.objects.create(
            name="Note de frais", code="EXPENSE",
            form_schema={"fields": [
                {"name": "montant", "type": "decimal", "required": True},
                {"name": "motif", "type": "text", "required": True},
            ]},
        )
        self.client.login(username="employee1", password="x")

    def test_saving_draft_does_not_require_mandatory_fields(self):
        response = self.client.post(
            f"/new/{self.request_type.id}/", {"action": "draft", "montant": "", "motif": ""}
        )
        self.assertEqual(response.status_code, 302)
        req = Request.objects.get(requester=self.employee)
        self.assertEqual(req.status, Request.Status.DRAFT)
        self.assertIsNone(req.submitted_at)

    def test_draft_appears_in_my_requests_with_continue_and_delete_links(self):
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, status=Request.Status.DRAFT,
        )
        response = self.client.get("/mine/")
        self.assertContains(response, "Continuer")
        self.assertContains(response, "Supprimer")
        self.assertContains(response, f"/{req.pk}/edit/")

    def test_continuing_a_draft_prefills_the_form(self):
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, status=Request.Status.DRAFT,
            data={"montant": "200", "motif": "brouillon initial"},
        )
        response = self.client.get(f"/{req.pk}/edit/")
        self.assertContains(response, "brouillon initial")

    def test_submitting_a_completed_draft_moves_it_to_pending(self):
        UserProfile.objects.create(user=self.employee, manager=self.other_user)
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"}
        )
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, status=Request.Status.DRAFT,
            data={"montant": "200"},
        )
        response = self.client.post(
            f"/{req.pk}/edit/", {"action": "submit", "montant": "200", "motif": "complet"}
        )
        self.assertEqual(response.status_code, 302)
        req.refresh_from_db()
        self.assertEqual(req.status, Request.Status.PENDING)
        self.assertIsNotNone(req.submitted_at)

    def test_deleting_a_draft_removes_it(self):
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, status=Request.Status.DRAFT,
        )
        response = self.client.post(f"/{req.pk}/delete/")
        self.assertEqual(response.status_code, 302)
        self.assertFalse(Request.objects.filter(pk=req.pk).exists())

    def test_cannot_delete_a_submitted_request(self):
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, status=Request.Status.PENDING,
        )
        response = self.client.post(f"/{req.pk}/delete/", follow=True)
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "ne peut plus être supprimée")
        self.assertTrue(Request.objects.filter(pk=req.pk).exists())

    def test_cannot_delete_someone_elses_draft(self):
        req = Request.objects.create(
            request_type=self.request_type, requester=self.other_user, status=Request.Status.DRAFT,
        )
        response = self.client.post(f"/{req.pk}/delete/")
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Request.objects.filter(pk=req.pk).exists())

    def test_cannot_edit_someone_elses_draft(self):
        req = Request.objects.create(
            request_type=self.request_type, requester=self.other_user, status=Request.Status.DRAFT,
        )
        response = self.client.get(f"/{req.pk}/edit/")
        self.assertEqual(response.status_code, 403)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class RequestAttachmentTests(TestCase):
    """Pièces jointes libres sur une demande (retour client) : disponibles pour
    tous les types, pas seulement congés — un fichier invalide bloque toute la
    soumission (tout ou rien), rien n'est enregistré à moitié."""

    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def setUp(self):
        self.employee = User.objects.create_user("employee1", password="x")
        self.request_type = RequestType.objects.create(
            name="Congés", code="LEAVE", is_active=True,
            form_schema={"fields": [{"name": "motif", "type": "text", "required": False}]},
        )
        self.client.login(username="employee1", password="x")

    def _pdf(self, name="justificatif.pdf"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, b"%PDF-1.4 minimal", content_type="application/pdf")

    def test_valid_attachment_saved_on_submit(self):
        response = self.client.post(
            f"/new/{self.request_type.id}/",
            {"action": "submit", "motif": "test", "attachments": [self._pdf()]},
        )
        self.assertEqual(response.status_code, 302)
        req = Request.objects.get(requester=self.employee)
        self.assertEqual(req.attachments.count(), 1)
        self.assertTrue(req.attachments.first().file.name.endswith(".pdf"))

    def test_wrong_file_type_blocks_entire_submission(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        bad_file = SimpleUploadedFile("virus.exe", b"not allowed", content_type="application/octet-stream")
        response = self.client.post(
            f"/new/{self.request_type.id}/",
            {"action": "submit", "motif": "test", "attachments": [bad_file]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Request.objects.filter(requester=self.employee).exists())

    def test_oversized_attachment_blocks_entire_submission(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        big_file = SimpleUploadedFile(
            "big.pdf", b"%PDF-1.4 " + b"0" * (6 * 1024 * 1024), content_type="application/pdf"
        )
        response = self.client.post(
            f"/new/{self.request_type.id}/",
            {"action": "submit", "motif": "test", "attachments": [big_file]},
        )
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Request.objects.filter(requester=self.employee).exists())

    def test_draft_attachments_accumulate_across_saves(self):
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, status=Request.Status.DRAFT,
        )
        RequestAttachment.objects.create(request=req, file=self._pdf("first.pdf"), uploaded_by=self.employee)

        response = self.client.post(
            f"/{req.pk}/edit/",
            {"action": "draft", "motif": "", "attachments": [self._pdf("second.pdf")]},
        )
        self.assertEqual(response.status_code, 302)
        self.assertEqual(req.attachments.count(), 2)

    def test_attachment_visible_on_request_detail(self):
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, status=Request.Status.APPROVED,
        )
        RequestAttachment.objects.create(request=req, file=self._pdf("preuve.pdf"), uploaded_by=self.employee)
        response = self.client.get(f"/{req.pk}/")
        self.assertContains(response, "preuve")


class SidebarRoleLabelTests(TestCase):
    """La sidebar affichait le rôle SYSTÈME calculé (Demandeur/Manager/Admin
    fonctionnel/Super admin) — retour client : ça doit plutôt être le rôle
    métier librement assigné (UserProfile.role, ex: "Comptable"), et rester
    vide tant qu'aucun rôle n'a été assigné, pas de texte de repli."""

    def test_shows_assigned_role_name(self):
        from .models import Role

        role = Role.objects.create(name="Comptable")
        employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=employee, role=role)
        self.client.login(username="employee1", password="x")
        response = self.client.get("/")
        self.assertContains(response, "Comptable")

    def test_blank_when_no_role_assigned_not_system_role(self):
        """Ni "Demandeur", ni "Manager", ni aucun autre libellé système ne
        doit apparaître comme repli — juste vide."""
        manager = User.objects.create_user("manager1", password="x")
        employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=employee, manager=manager)  # a des rapports directs, mais pas de Role assigné
        self.client.login(username="manager1", password="x")
        response = self.client.get("/")
        self.assertNotContains(response, "Demandeur")
        self.assertNotContains(response, "Manager")


class LoginErrorStylingTests(TestCase):
    def test_invalid_login_error_uses_styled_errorlist(self):
        User.objects.create_user("employee1", password="x")
        response = self.client.post("/login/", {"username": "employee1", "password": "wrong"})
        self.assertContains(response, 'class="errorlist')


class MyRequestsSubmitButtonTests(TestCase):
    """Le bouton "Soumettre une demande" doit toujours être visible (pas
    seulement quand la liste est vide) et pointer directement vers le
    formulaire du type filtré, plutôt que vers l'accueil (retour client)."""

    def setUp(self):
        self.employee = User.objects.create_user("employee1", password="x")
        self.request_type = RequestType.objects.create(
            name="Congés", code="LEAVE", is_active=True, form_schema={"fields": []},
        )
        Request.objects.create(
            request_type=self.request_type, requester=self.employee, status=Request.Status.DRAFT,
        )
        self.client.login(username="employee1", password="x")

    def test_filtered_view_links_directly_to_that_types_create_form(self):
        response = self.client.get(f"/mine/?type={self.request_type.code}")
        self.assertContains(response, f"/new/{self.request_type.id}/")

    def test_unfiltered_view_links_to_dashboard(self):
        response = self.client.get("/mine/")
        self.assertContains(response, 'href="/"')


class PaginationTests(TestCase):
    """Le design n'avait été testé qu'avec 2-3 demandes (retour client) — au
    delà de LIST_PAGE_SIZE, les listes doivent être paginées plutôt que de
    tout afficher sur une seule page."""

    def setUp(self):
        self.employee = User.objects.create_user("employee1", password="x")
        self.request_type = RequestType.objects.create(
            name="Congés", code="LEAVE", is_active=True, form_schema={"fields": []},
        )
        for _ in range(20):
            Request.objects.create(
                request_type=self.request_type, requester=self.employee, status=Request.Status.APPROVED,
            )
        self.client.login(username="employee1", password="x")

    def test_my_requests_first_page_shows_only_page_size_items(self):
        from .views import LIST_PAGE_SIZE

        response = self.client.get("/mine/")
        self.assertEqual(len(response.context["requests"]), LIST_PAGE_SIZE)
        self.assertContains(response, "Page 1 / 2")

    def test_my_requests_second_page_shows_remaining_items(self):
        from .views import LIST_PAGE_SIZE

        response = self.client.get("/mine/?page=2")
        self.assertEqual(len(response.context["requests"]), 20 - LIST_PAGE_SIZE)

    def test_pending_approvals_is_paginated(self):
        manager = User.objects.create_user("manager1", password="x")
        UserProfile.objects.create(user=self.employee, manager=manager)
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"}
        )
        for _ in range(20):
            req = Request.objects.create(request_type=self.request_type, requester=self.employee)
            from .services import WorkflowEngine

            WorkflowEngine(req).submit()

        self.client.login(username="manager1", password="x")
        response = self.client.get("/pending/")
        from .views import LIST_PAGE_SIZE

        self.assertEqual(len(response.context["requests"]), LIST_PAGE_SIZE)


class SearchTests(TestCase):
    """Retour client : permettre de rechercher rapidement parmi ses demandes
    ou ses approbations, à la fois par libellé générique et par les champs
    propres à chaque type de demande (ex: "fournisseur" pour Achat IT,
    "motif" pour Congés — pas les mêmes informations d'un type à l'autre)."""

    def setUp(self):
        self.manager = User.objects.create_user("manager1", password="x")
        self.employee = User.objects.create_user(
            "employee1", password="x", first_name="Emma", last_name="Employe",
        )
        UserProfile.objects.create(user=self.employee, manager=self.manager)

        self.expense_type = RequestType.objects.create(
            name="Note de frais", code="EXPENSE",
            form_schema={"fields": [
                {"name": "montant", "type": "decimal", "label": "Montant"},
                {"name": "motif", "type": "text", "label": "Motif"},
            ]},
        )
        self.it_type = RequestType.objects.create(
            name="Achat Fournisseur IT", code="PURCHASE_IT",
            form_schema={"fields": [
                {"name": "fournisseur", "type": "text", "label": "Fournisseur"},
            ]},
        )
        ApprovalRule.objects.create(
            request_type=self.expense_type, level=1, criteria={}, approvers_config={"type": "manager"},
        )
        ApprovalRule.objects.create(
            request_type=self.it_type, level=1, criteria={}, approvers_config={"type": "manager"},
        )

        self.expense_request = Request.objects.create(
            request_type=self.expense_type, requester=self.employee,
            data={"montant": 120, "motif": "Repas d'affaires avec un client"},
        )
        self.it_request = Request.objects.create(
            request_type=self.it_type, requester=self.employee,
            data={"fournisseur": "Dell Canada"},
        )

    @staticmethod
    def _result_codes(response):
        # "Note de frais"/"Achat Fournisseur IT" apparaissent aussi dans les
        # liens de la barre latérale, présents sur toute la page quel que
        # soit le résultat de recherche : on vérifie donc les résultats
        # réels via le contexte plutôt que le texte brut de la page.
        return {req.request_type.code for req in response.context["requests"]}

    def test_my_requests_search_matches_field_specific_to_request_type(self):
        self.client.login(username="employee1", password="x")

        response = self.client.get("/mine/?q=dell")
        self.assertEqual(self._result_codes(response), {"PURCHASE_IT"})

        response = self.client.get("/mine/?q=repas")
        self.assertEqual(self._result_codes(response), {"EXPENSE"})

    def test_my_requests_search_is_case_insensitive_and_combines_terms(self):
        self.client.login(username="employee1", password="x")
        response = self.client.get("/mine/?q=REPAS client")
        self.assertContains(response, "Note de frais")

    def test_my_requests_search_with_no_match_shows_empty_state(self):
        self.client.login(username="employee1", password="x")
        response = self.client.get("/mine/?q=introuvable")
        self.assertContains(response, "Aucune demande ne correspond")

    def test_pending_approvals_search_matches_requester_name(self):
        from .services import WorkflowEngine

        WorkflowEngine(self.expense_request).submit()
        WorkflowEngine(self.it_request).submit()

        self.client.login(username="manager1", password="x")
        response = self.client.get("/pending/?q=emma")
        self.assertEqual(self._result_codes(response), {"EXPENSE", "PURCHASE_IT"})

        response = self.client.get("/pending/?q=dell")
        self.assertEqual(self._result_codes(response), {"PURCHASE_IT"})

    def test_search_combines_with_type_filter(self):
        from .services import WorkflowEngine

        WorkflowEngine(self.expense_request).submit()

        self.client.login(username="manager1", password="x")
        # "emma" matche le demandeur, mais le filtre type=PURCHASE_IT exclut EXPENSE
        response = self.client.get("/pending/?type=PURCHASE_IT&q=emma")
        self.assertEqual(self._result_codes(response), set())


class RequestListAllAttributesTests(TestCase):
    """Retour client : les listes "Mes demandes" et "À approuver" doivent
    afficher tous les attributs des demandes — les champs du modèle Request
    (id, dates...) et ceux propres au formulaire de chaque type (via
    labeled_data), pas seulement le sous-ensemble affiché auparavant."""

    def setUp(self):
        self.manager = User.objects.create_user("manager1", password="x")
        self.employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=self.employee, manager=self.manager)
        self.request_type = RequestType.objects.create(
            name="Note de frais", code="EXPENSE",
            form_schema={"fields": [{"name": "motif", "type": "text", "label": "Motif"}]},
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"},
        )
        self.req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, data={"motif": "Taxi aéroport"},
        )

    def test_my_requests_shows_id_dates_and_labeled_fields(self):
        self.client.login(username="employee1", password="x")
        response = self.client.get("/mine/")
        self.assertContains(response, str(self.req.id)[:8])
        self.assertContains(response, "Créée le")
        self.assertContains(response, "Complétée le")
        self.assertContains(response, "Motif")
        self.assertContains(response, "Taxi aéroport")

    def test_pending_approvals_shows_id_dates_and_labeled_fields(self):
        from .services import WorkflowEngine

        WorkflowEngine(self.req).submit()
        self.client.login(username="manager1", password="x")
        response = self.client.get("/pending/")
        self.assertContains(response, str(self.req.id)[:8])
        self.assertContains(response, "Créée le")
        self.assertContains(response, "Motif")
        self.assertContains(response, "Taxi aéroport")


class NextRequestLinkTests(TestCase):
    """Après une décision, l'approbateur doit pouvoir enchaîner directement sur
    une autre demande du même type sans repasser par la liste (retour client)."""

    def setUp(self):
        self.manager = User.objects.create_user("manager1", password="x")
        self.employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=self.employee, manager=self.manager)
        self.request_type = RequestType.objects.create(
            name="Congés", code="LEAVE", is_active=True, form_schema={"fields": []},
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"}
        )

    def _submit(self):
        from .services import WorkflowEngine

        req = Request.objects.create(request_type=self.request_type, requester=self.employee)
        WorkflowEngine(req).submit()
        return req

    def test_shows_link_to_next_pending_request_of_same_type(self):
        req1 = self._submit()
        req2 = self._submit()
        self.client.login(username="manager1", password="x")

        response = self.client.get(f"/{req1.pk}/")
        self.assertContains(response, f"/{req2.pk}/")
        self.assertContains(response, "Traiter la demande suivante")

    def test_falls_back_to_list_when_no_other_pending_request(self):
        req = self._submit()
        self.client.login(username="manager1", password="x")

        response = self.client.get(f"/{req.pk}/")
        self.assertContains(response, "Retour à la liste des demandes à approuver")

    def test_no_next_request_section_for_the_requester(self):
        req = self._submit()
        self.client.login(username="employee1", password="x")

        response = self.client.get(f"/{req.pk}/")
        self.assertNotContains(response, "card-title\">Suite<")


class ReportsPageTests(TestCase):
    """Retour client : les rapports doivent être présentés sous forme de vrais
    graphiques (barres), pas de lignes de texte brutes."""

    def test_reports_page_renders_chart_components(self):
        staff = User.objects.create_user("staff1", password="x", is_staff=True)
        request_type = RequestType.objects.create(name="Congés", code="LEAVE", form_schema={"fields": []})
        req = Request.objects.create(
            request_type=request_type, requester=staff, status=Request.Status.APPROVED,
        )
        from django.utils import timezone
        req.submitted_at = timezone.now()
        req.completed_at = timezone.now()
        req.save()

        self.client.login(username="staff1", password="x")
        response = self.client.get("/rapports/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "chart-v")
        self.assertContains(response, "chart-row")
        self.assertContains(response, "stat-grid")

    def test_reports_page_shows_empty_state_without_data(self):
        staff = User.objects.create_user("staff1", password="x", is_staff=True)
        self.client.login(username="staff1", password="x")
        response = self.client.get("/rapports/")
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Aucune donnée pour le moment.")


class LoginRedirectTests(TestCase):
    def test_visiting_login_page_while_authenticated_redirects_to_dashboard(self):
        User.objects.create_user("someone", password="x")
        self.client.login(username="someone", password="x")
        response = self.client.get("/login/")
        self.assertRedirects(response, "/")


class Handler400Tests(TestCase):
    """Retour déploiement : Django n'affiche par défaut aucun détail sur un
    400 (page brute "Bad Request (400)") — un fichier joint trop volumineux
    atterrissait ici sans message compréhensible pour l'utilisateur."""

    def _request(self):
        # RequestFactory ne fait pas tourner AuthenticationMiddleware (contrairement
        # à une vraie requête HTTP) : le context processor sidebar a besoin de
        # request.user, qu'on doit donc poser nous-mêmes ici.
        from django.contrib.auth.models import AnonymousUser
        from django.test import RequestFactory

        request = RequestFactory().get("/")
        request.user = AnonymousUser()
        return request

    def test_request_data_too_big_shows_actionable_message(self):
        from django.core.exceptions import RequestDataTooBig

        from approvals.views import handler400

        response = handler400(self._request(), exception=RequestDataTooBig())
        self.assertEqual(response.status_code, 400)
        self.assertIn("trop volumineux", response.content.decode())

    def test_other_bad_request_shows_generic_message_without_leaking_exception(self):
        from approvals.views import handler400

        response = handler400(self._request(), exception=Exception("détail interne sensible"))
        self.assertEqual(response.status_code, 400)
        self.assertNotIn("détail interne sensible", response.content.decode())


class ApproverFallbackTests(TestCase):
    """Retour client : un nouvel employé sans manager, quand personne (admin,
    directeur, délégué...) n'est disponible pour corriger son profil, doit
    pouvoir choisir lui-même un approbateur de secours plutôt que rester
    bloqué. Devient son manager permanent une fois choisi."""

    def setUp(self):
        self.no_manager_user = User.objects.create_user(
            "nicolas", password="x", first_name="Nicolas", last_name="Nouveau",
        )
        self.staff_active_today = User.objects.create_user(
            "admin_fonc", password="x", first_name="Alice", last_name="Admin", is_staff=True,
        )
        self.staff_inactive = User.objects.create_user(
            "admin_inactif", password="x", first_name="Bob", last_name="Inactif", is_staff=True,
        )
        UserProfile.objects.create(
            user=self.staff_active_today, last_seen_at=timezone.now(),
        )
        # Simple demandeur : n'a aucune autorité d'approbation, ne doit
        # jamais apparaître comme candidat.
        self.plain_employee = User.objects.create_user("employee_lambda", password="x")

        self.request_type = RequestType.objects.create(
            name="Congés", code="LEAVE", form_schema={"fields": []},
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"},
        )
        self.client.login(username="nicolas", password="x")

    def test_submitting_without_manager_shows_approver_picker(self):
        response = self.client.post(
            f"/new/{self.request_type.id}/", {"action": "submit"},
        )
        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Choisis un approbateur pour continuer")
        self.assertContains(response, "Alice Admin")
        # Un simple demandeur (aucune autorité d'approbation) n'est jamais candidat.
        self.assertNotContains(response, "employee_lambda")

    def test_choosing_an_approver_submits_and_sets_permanent_manager(self):
        response = self.client.post(
            f"/new/{self.request_type.id}/",
            {"action": "submit", "chosen_approver_id": self.staff_active_today.id},
            follow=True,
        )
        self.assertContains(response, "Demande soumise avec succès")

        profile = UserProfile.objects.get(user=self.no_manager_user)
        self.assertEqual(profile.manager_id, self.staff_active_today.id)

        # Une prochaine demande ne redemande plus le choix : le manager est déjà assigné,
        # donc la soumission réussit directement (redirection), sans repasser par le sélecteur.
        response2 = self.client.post(f"/new/{self.request_type.id}/", {"action": "submit"})
        self.assertEqual(response2.status_code, 302)

    def test_cannot_choose_a_plain_employee_as_approver(self):
        """Un ID choisi qui ne correspond à aucun candidat légitime (ex:
        falsifié) est ignoré — retombe sur le sélecteur, ne l'assigne pas."""
        response = self.client.post(
            f"/new/{self.request_type.id}/",
            {"action": "submit", "chosen_approver_id": self.plain_employee.id},
        )
        self.assertContains(response, "Choisis un approbateur pour continuer")
        profile = UserProfile.objects.get(user=self.no_manager_user)
        self.assertIsNone(profile.manager_id)

    def test_falls_back_to_all_candidates_when_no_one_active_today(self):
        """Si personne n'a navigué aujourd'hui, la liste complète des
        candidats potentiels s'affiche plutôt qu'un blocage total."""
        UserProfile.objects.filter(user=self.staff_active_today).update(last_seen_at=None)
        response = self.client.post(f"/new/{self.request_type.id}/", {"action": "submit"})
        self.assertContains(response, "Alice Admin")
        self.assertContains(response, "Bob Inactif")
        self.assertContains(response, "pas d'activité aujourd'hui")


class TrackLastSeenMiddlewareTests(TestCase):
    def test_first_authenticated_request_creates_profile_with_last_seen(self):
        user = User.objects.create_user("freshuser", password="x")
        self.assertFalse(UserProfile.objects.filter(user=user).exists())
        self.client.login(username="freshuser", password="x")
        self.client.get("/")
        profile = UserProfile.objects.get(user=user)
        self.assertIsNotNone(profile.last_seen_at)

    def test_recent_last_seen_is_not_rewritten_on_every_request(self):
        user = User.objects.create_user("throttleduser", password="x")
        stamp = timezone.now() - datetime.timedelta(minutes=1)
        UserProfile.objects.create(user=user, last_seen_at=stamp)
        self.client.login(username="throttleduser", password="x")
        self.client.get("/")
        profile = UserProfile.objects.get(user=user)
        self.assertEqual(profile.last_seen_at, stamp)

    def test_stale_last_seen_is_refreshed(self):
        user = User.objects.create_user("staleuser", password="x")
        stale = timezone.now() - datetime.timedelta(hours=1)
        UserProfile.objects.create(user=user, last_seen_at=stale)
        self.client.login(username="staleuser", password="x")
        self.client.get("/")
        profile = UserProfile.objects.get(user=user)
        self.assertGreater(profile.last_seen_at, stale)


@override_settings(MEDIA_ROOT=tempfile.mkdtemp())
class ReferenceFormPdfTests(TestCase):
    """Un type de demande peut avoir un PDF de référence (ex: le formulaire
    papier existant), affiché au demandeur (formulaire de saisie) et à
    l'approbateur (détail de la demande) — à titre d'information seulement,
    la saisie reste faite via form_schema."""

    @classmethod
    def tearDownClass(cls):
        from django.conf import settings

        shutil.rmtree(settings.MEDIA_ROOT, ignore_errors=True)
        super().tearDownClass()

    def _tiny_pdf(self, name="reference.pdf"):
        from django.core.files.uploadedfile import SimpleUploadedFile

        return SimpleUploadedFile(name, b"%PDF-1.4 fake content", content_type="application/pdf")

    def setUp(self):
        self.employee = User.objects.create_user("employee_pdf", password="x")
        self.request_type = RequestType.objects.create(
            name="Nouveau rapport", code="REPORT", form_schema={"fields": []}
        )
        self.client.login(username="employee_pdf", password="x")

    def test_reference_pdf_shown_on_request_form_when_configured(self):
        self.request_type.reference_form_pdf.save("reference.pdf", self._tiny_pdf(), save=True)
        response = self.client.get(f"/new/{self.request_type.id}/")
        self.assertContains(response, "Document de référence")
        self.assertContains(response, self.request_type.reference_form_pdf.url)

    def test_no_reference_pdf_panel_when_not_configured(self):
        response = self.client.get(f"/new/{self.request_type.id}/")
        self.assertNotContains(response, "Document de référence")

    def test_reference_pdf_shown_on_request_detail(self):
        self.request_type.reference_form_pdf.save("reference.pdf", self._tiny_pdf(), save=True)
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, data={}
        )
        response = self.client.get(f"/{req.pk}/")
        self.assertContains(response, "Document de référence")
        self.assertContains(response, self.request_type.reference_form_pdf.url)


class RequestSummaryPdfDownloadTests(TestCase):
    """Retour client : en plus du PDF de référence (lecture seule), un PDF
    résumé des réponses saisies doit être téléchargeable — généré à la volée,
    pas une copie remplie du PDF de référence (voir approvals/pdf_export.py)."""

    def setUp(self):
        self.employee = User.objects.create_user("employee_dl", password="x")
        self.manager = User.objects.create_user("manager_dl", password="x")
        self.stranger = User.objects.create_user("stranger_dl", password="x")
        UserProfile.objects.create(user=self.employee, manager=self.manager)
        self.request_type = RequestType.objects.create(
            name="Nouveau rapport",
            code="REPORT",
            form_schema={"fields": [{"name": "departement", "type": "text", "label": "Département", "required": True}]},
        )
        from .models import ApprovalRule

        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"}
        )
        self.req = Request.objects.create(
            request_type=self.request_type,
            requester=self.employee,
            data={"departement": "Ventes"},
        )
        from .services import WorkflowEngine

        WorkflowEngine(self.req).submit(actor=self.employee)

    def test_requester_can_download_pdf_with_answers(self):
        self.client.login(username="employee_dl", password="x")
        response = self.client.get(f"/{self.req.pk}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertTrue(response.content.startswith(b"%PDF"))

    def test_pdf_generation_does_not_crash_on_empty_field(self):
        """labeled_data() affiche un tiret cadratin (—, hors Latin-1) pour une
        valeur vide — la police de base du PDF (Helvetica) ne le supporte pas,
        ça faisait planter la génération (UnicodeEncodeError) plutôt que de
        l'afficher comme un simple "-"."""
        self.request_type.form_schema = {
            "fields": [
                {"name": "departement", "type": "text", "label": "Département", "required": True},
                {"name": "commentaire", "type": "text", "label": "Commentaire", "required": False},
            ]
        }
        self.request_type.save()
        req = Request.objects.create(
            request_type=self.request_type, requester=self.employee, data={"departement": "Ventes"}
        )
        from .services import WorkflowEngine

        WorkflowEngine(req).submit(actor=self.employee)

        self.client.login(username="employee_dl", password="x")
        response = self.client.get(f"/{req.pk}/pdf/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "application/pdf")
        self.assertIn(req.reference.encode(), response.content)
        self.assertIn(b"Ventes", response.content)

    def test_approver_can_download_pdf(self):
        self.client.login(username="manager_dl", password="x")
        response = self.client.get(f"/{self.req.pk}/pdf/")
        self.assertEqual(response.status_code, 200)

    def test_unrelated_user_cannot_download_pdf(self):
        self.client.login(username="stranger_dl", password="x")
        response = self.client.get(f"/{self.req.pk}/pdf/")
        self.assertEqual(response.status_code, 403)

    def test_download_link_shown_on_submitted_request_not_on_draft(self):
        self.client.login(username="employee_dl", password="x")
        response = self.client.get(f"/{self.req.pk}/")
        self.assertContains(response, "Télécharger le PDF")

        draft = Request.objects.create(
            request_type=self.request_type, requester=self.employee, status=Request.Status.DRAFT, data={}
        )
        response = self.client.get(f"/{draft.pk}/")
        self.assertNotContains(response, "Télécharger le PDF")
