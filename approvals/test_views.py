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

from .models import ApprovalRule, Request, RequestType, UserProfile


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

        response = self.client.post("/profil/", {"photo": self._tiny_png()}, follow=True)
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
        response = self.client.post("/profil/", {"photo": big_file})
        self.assertEqual(response.status_code, 200)  # ré-affiche le formulaire avec l'erreur
        self.assertFalse(UserProfile.objects.get(user=user).photo)

    def test_wrong_file_type_rejected(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        user = User.objects.create_user("photo_user4", password="x")
        self.client.login(username="photo_user4", password="x")
        bad_file = SimpleUploadedFile("notes.txt", b"pas une image", content_type="text/plain")
        response = self.client.post("/profil/", {"photo": bad_file})
        self.assertEqual(response.status_code, 200)
        self.assertFalse(UserProfile.objects.get(user=user).photo)


class LoginRedirectTests(TestCase):
    def test_visiting_login_page_while_authenticated_redirects_to_dashboard(self):
        User.objects.create_user("someone", password="x")
        self.client.login(username="someone", password="x")
        response = self.client.get("/login/")
        self.assertRedirects(response, "/")
