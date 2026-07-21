"""Inscription en ligne, activation par un admin, et double authentification
par email à la connexion (retour client)."""
import datetime

from django.contrib.auth.models import User
from django.core import mail
from django.test import Client, TestCase
from django.utils import timezone

from . import crypto
from .models import EmailSettings, EmailToken, UserProfile


class PasswordResetTemplateTests(TestCase):
    """django.contrib.admin fournit ses propres registration/password_reset_*.html ;
    si `approvals` n'est pas déclarée avant `django.contrib.admin` dans
    INSTALLED_APPS, l'app_directories loader résout nos vues de réinitialisation
    de mot de passe vers les templates admin (page "Site d'administration de
    Django") au lieu des nôtres. Régression trouvée en test manuel."""

    def test_password_reset_form_uses_app_template_not_admin_template(self):
        response = self.client.get("/mot-de-passe/reinitialiser/")
        self.assertNotContains(response, "Site d’administration de Django")
        self.assertContains(response, "Mot de passe oublié")

    def test_password_reset_done_uses_app_template(self):
        response = self.client.get("/mot-de-passe/reinitialiser/envoye/")
        self.assertNotContains(response, "Site d’administration de Django")
        self.assertContains(response, "Retour à la connexion")


class RegistrationFlowTests(TestCase):
    def setUp(self):
        self.client = Client()

    def test_registration_creates_inactive_user_and_sends_confirmation_email(self):
        response = self.client.post("/inscription/", {
            "username": "nouvel_employe",
            "first_name": "Nadia",
            "last_name": "Employe",
            "email": "nadia@example.com",
            "password": "correcthorsebattery123",
            "password_confirm": "correcthorsebattery123",
        })
        self.assertEqual(response.status_code, 200)  # registration_pending.html

        user = User.objects.get(username="nouvel_employe")
        self.assertFalse(user.is_active)
        self.assertTrue(user.check_password("correcthorsebattery123"))
        self.assertEqual(len(mail.outbox), 1)
        self.assertIn("nadia@example.com", mail.outbox[0].to)

        profile = UserProfile.objects.get(user=user)
        self.assertIsNone(profile.email_confirmed_at)

    def test_duplicate_username_rejected(self):
        User.objects.create_user("existant", password="x")
        response = self.client.post("/inscription/", {
            "username": "existant",
            "first_name": "A",
            "last_name": "B",
            "email": "a@example.com",
            "password": "correcthorsebattery123",
            "password_confirm": "correcthorsebattery123",
        })
        self.assertContains(response, "déjà pris")
        self.assertEqual(User.objects.filter(username="existant").count(), 1)

    def test_mismatched_passwords_rejected(self):
        response = self.client.post("/inscription/", {
            "username": "test2",
            "first_name": "A",
            "last_name": "B",
            "email": "a2@example.com",
            "password": "correcthorsebattery123",
            "password_confirm": "autrechosecomplet",
        })
        self.assertContains(response, "ne correspondent pas")
        self.assertFalse(User.objects.filter(username="test2").exists())

    def test_confirming_email_does_not_activate_account(self):
        user = User.objects.create_user("pending_user", password="x", is_active=False)
        UserProfile.objects.create(user=user)
        token = EmailToken.objects.create(user=user, purpose=EmailToken.Purpose.EMAIL_CONFIRM)

        response = self.client.get(f"/confirmer-email/{token.token}/", follow=True)
        self.assertContains(response, "administrateur doit maintenant activer")

        user.refresh_from_db()
        self.assertFalse(user.is_active)
        self.assertIsNotNone(user.profile.email_confirmed_at)

    def test_expired_or_used_token_rejected(self):
        user = User.objects.create_user("pending_user2", password="x", is_active=False)
        UserProfile.objects.create(user=user)
        token = EmailToken.objects.create(user=user, purpose=EmailToken.Purpose.EMAIL_CONFIRM)
        token.mark_used()

        response = self.client.get(f"/confirmer-email/{token.token}/", follow=True)
        self.assertContains(response, "n&#x27;est plus valide")
        user.refresh_from_db()
        self.assertIsNone(user.profile.email_confirmed_at)


class AccountActivationAdminTests(TestCase):
    def setUp(self):
        self.admin = User.objects.create_superuser("admin", password="x")
        self.client = Client()
        self.client.force_login(self.admin)

        self.confirmed_user = User.objects.create_user("confirmed", password="x", is_active=False)
        self.confirmed_profile = UserProfile.objects.create(
            user=self.confirmed_user, email_confirmed_at=timezone.now()
        )
        self.unconfirmed_user = User.objects.create_user("unconfirmed", password="x", is_active=False)
        self.unconfirmed_profile = UserProfile.objects.create(user=self.unconfirmed_user)

    def test_activation_action_only_activates_confirmed_accounts(self):
        from .admin import UserProfileAdmin
        from django.contrib.admin.sites import site

        admin_instance = UserProfileAdmin(UserProfile, site)
        qs = UserProfile.objects.filter(pk__in=[self.confirmed_profile.pk, self.unconfirmed_profile.pk])

        request = type("R", (), {"user": self.admin})()
        # unfold's ModelAdmin.message_user relies on django.contrib.messages middleware;
        # exercise the action directly against the queryset instead of via HTTP.
        from django.contrib.messages.storage.fallback import FallbackStorage
        from django.test import RequestFactory

        http_request = RequestFactory().get("/")
        http_request.user = self.admin
        http_request.session = self.client.session
        http_request._messages = FallbackStorage(http_request)

        admin_instance.activer_les_comptes(http_request, qs)

        self.confirmed_user.refresh_from_db()
        self.unconfirmed_user.refresh_from_db()
        self.assertTrue(self.confirmed_user.is_active)
        self.assertFalse(self.unconfirmed_user.is_active)


class LoginConfirmationTests(TestCase):
    def setUp(self):
        self.user = User.objects.create_user("employee1", password="secret1234", email="e1@example.com")
        self.client = Client()

    def test_login_without_active_email_config_logs_in_directly(self):
        response = self.client.post("/login/", {"username": "employee1", "password": "secret1234"}, follow=True)
        self.assertTrue(response.context["user"].is_authenticated)

    def test_login_with_confirmation_required_defers_login(self):
        EmailSettings.objects.create(
            label="Test", is_active=True, host="smtp.example.com", from_email="noreply@example.com",
            require_login_confirmation=True,
        )
        response = self.client.post("/login/", {"username": "employee1", "password": "secret1234"})
        self.assertContains(response, self.user.email)
        self.assertFalse(response.wsgi_request.user.is_authenticated)
        self.assertEqual(len(mail.outbox), 1)

        token = EmailToken.objects.get(user=self.user, purpose=EmailToken.Purpose.LOGIN_CONFIRM)
        confirm_response = self.client.get(f"/connexion/confirmer/{token.token}/", follow=True)
        self.assertTrue(confirm_response.context["user"].is_authenticated)
        self.assertEqual(confirm_response.context["user"], self.user)

    def test_login_confirmation_link_expires(self):
        EmailSettings.objects.create(
            label="Test", is_active=True, host="smtp.example.com", from_email="noreply@example.com",
            require_login_confirmation=True,
        )
        token = EmailToken.objects.create(
            user=self.user, purpose=EmailToken.Purpose.LOGIN_CONFIRM,
            backend_path="django.contrib.auth.backends.ModelBackend",
        )
        token.created_at = timezone.now() - datetime.timedelta(minutes=20)
        token.save(update_fields=["created_at"])

        response = self.client.get(f"/connexion/confirmer/{token.token}/", follow=True)
        self.assertFalse(response.context["user"].is_authenticated)
        self.assertContains(response, "expiré")

    def test_require_login_confirmation_can_be_disabled(self):
        EmailSettings.objects.create(
            label="Test", is_active=True, host="smtp.example.com", from_email="noreply@example.com",
            require_login_confirmation=False,
        )
        response = self.client.post("/login/", {"username": "employee1", "password": "secret1234"}, follow=True)
        self.assertTrue(response.context["user"].is_authenticated)
        self.assertEqual(len(mail.outbox), 0)


class EmailSettingsModelTests(TestCase):
    def test_password_round_trips_through_encryption(self):
        settings_obj = EmailSettings(
            label="Gmail", host="smtp.gmail.com", from_email="a@gmail.com",
        )
        settings_obj.password = "app-password-123"
        settings_obj.save()

        reloaded = EmailSettings.objects.get(pk=settings_obj.pk)
        self.assertEqual(reloaded.password, "app-password-123")
        self.assertNotIn("app-password-123", reloaded._password_encrypted)

    def test_only_one_active_configuration_at_a_time(self):
        first = EmailSettings.objects.create(
            label="Gmail", is_active=True, host="smtp.gmail.com", from_email="a@gmail.com"
        )
        second = EmailSettings.objects.create(
            label="Exchange", is_active=True, host="smtp.office365.com", from_email="b@example.com"
        )
        first.refresh_from_db()
        second.refresh_from_db()
        self.assertFalse(first.is_active)
        self.assertTrue(second.is_active)
        self.assertEqual(EmailSettings.get_active(), second)

    def test_crypto_helpers_round_trip(self):
        encrypted = crypto.encrypt("hunter2")
        self.assertNotEqual(encrypted, "hunter2")
        self.assertEqual(crypto.decrypt(encrypted), "hunter2")
        self.assertEqual(crypto.encrypt(""), "")
        self.assertEqual(crypto.decrypt(""), "")
