"""Tests de bout en bout via le client de test Django (requêtes HTTP réelles).

Ajouté après un bug en revue client : un approbateur intermédiaire recevait un
403 Forbidden juste après avoir approuvé un niveau, parce que la vue le
redirigeait vers le détail de la demande alors qu'il n'était plus autorisé à
la consulter (elle était passée au niveau suivant). Les tests sur
WorkflowEngine seul ne pouvaient pas détecter ça : le moteur fonctionnait
très bien, c'est l'enchaînement décision -> redirection -> permission de vue
qui était cassé.
"""
import shutil
import tempfile

from django.contrib.auth.models import User
from django.test import Client, TestCase, override_settings

from .models import ApprovalRule, Request, RequestAttachment, RequestType, UserProfile


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

    def test_manager_department_site_not_editable_via_this_form(self):
        manager = User.objects.create_user("manager1", password="x")
        UserProfile.objects.create(user=self.user, manager=manager, department_id=10)
        response = self.client.post("/profil/", {
            "action": "update_info",
            "username": "employee1", "first_name": "Old", "last_name": "Name", "email": "old@example.com",
            "manager": "", "department_id": "999",
        })
        self.assertEqual(response.status_code, 302)
        profile = UserProfile.objects.get(user=self.user)
        self.assertEqual(profile.manager_id, manager.id)
        self.assertEqual(profile.department_id, 10)


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
    """La sidebar affichait 'Utilisateur' pour tout le monde (retour client) :
    doit maintenant afficher le rôle réel (Manager pour qui a des rapports
    directs, Demandeur sinon)."""

    def test_manager_sees_manager_label(self):
        manager = User.objects.create_user("manager1", password="x")
        employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=employee, manager=manager)
        self.client.login(username="manager1", password="x")
        response = self.client.get("/")
        self.assertContains(response, "Manager")

    def test_plain_employee_sees_demandeur_label(self):
        User.objects.create_user("employee1", password="x")
        self.client.login(username="employee1", password="x")
        response = self.client.get("/")
        self.assertContains(response, "Demandeur")
        self.assertNotContains(response, "Utilisateur")


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


class LoginRedirectTests(TestCase):
    def test_visiting_login_page_while_authenticated_redirects_to_dashboard(self):
        User.objects.create_user("someone", password="x")
        self.client.login(username="someone", password="x")
        response = self.client.get("/login/")
        self.assertRedirects(response, "/")
