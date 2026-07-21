"""Inscription en ligne et connexion à deux facteurs par email (retour client) :

- L'employé crée son compte et confirme son adresse email via un lien.
- L'activation du compte (et l'assignation d'un rôle/manager/département)
  reste une action distincte réservée à un admin fonctionnel (voir
  UserProfileAdmin.activer_les_comptes dans admin.py) — la confirmation
  d'email seule ne permet jamais de se connecter.
- À la connexion, si une configuration email active l'exige
  (EmailSettings.require_login_confirmation), un lien de confirmation est
  envoyé par email et doit être cliqué avant que la session ne s'ouvre
  (double authentification par email).
"""
import logging

from django import forms
from django.contrib import messages
from django.contrib.auth import login as auth_login
from django.contrib.auth.forms import AuthenticationForm
from django.contrib.auth.models import User
from django.core.mail import send_mail
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse
from django.utils import timezone

from .models import EmailSettings, EmailToken, UserProfile

logger = logging.getLogger(__name__)


class RegistrationForm(forms.Form):
    username = forms.CharField(max_length=150, label="Nom d'utilisateur")
    first_name = forms.CharField(max_length=150, label="Prénom")
    last_name = forms.CharField(max_length=150, label="Nom")
    email = forms.EmailField(label="Email")
    password = forms.CharField(widget=forms.PasswordInput, label="Mot de passe")
    password_confirm = forms.CharField(widget=forms.PasswordInput, label="Confirmer le mot de passe")

    def clean_username(self):
        username = self.cleaned_data["username"].strip().lower()
        if User.objects.filter(username=username).exists():
            raise forms.ValidationError("Ce nom d'utilisateur est déjà pris.")
        return username

    def clean_email(self):
        email = self.cleaned_data["email"].strip().lower()
        if User.objects.filter(email=email).exists():
            raise forms.ValidationError("Un compte existe déjà avec cet email.")
        return email

    def clean(self):
        cleaned = super().clean()
        password = cleaned.get("password")
        confirm = cleaned.get("password_confirm")
        if password and confirm and password != confirm:
            self.add_error("password_confirm", "Les mots de passe ne correspondent pas.")
        return cleaned


#inscription

def register(request):
    if request.method == "POST":
        form = RegistrationForm(request.POST)
        if form.is_valid():
            data = form.cleaned_data
            user = User.objects.create_user(
                username=data["username"],
                email=data["email"],
                password=data["password"],
                first_name=data["first_name"],
                last_name=data["last_name"],
                is_active=False,
            )
            UserProfile.objects.get_or_create(user=user)
            if not _send_confirmation_email(request, user):
                messages.warning(
                    request,
                    "Le compte a été créé, mais l'email de confirmation n'a pas pu être envoyé "
                    "(problème de configuration du serveur de messagerie). Contacte un administrateur "
                    "pour qu'il confirme et active ton compte manuellement.",
                )
            return render(request, "approvals/registration_pending.html", {"email": user.email})
    else:
        form = RegistrationForm()
    return render(request, "approvals/register.html", {"form": form})


#mail d'envoi de confirmation de mail
def _send_confirmation_email(request, user):
    """Retourne False (sans lever d'exception) si l'envoi échoue — une config
    SMTP invalide ne doit jamais faire planter la page pour l'utilisateur."""
    token = EmailToken.objects.create(user=user, purpose=EmailToken.Purpose.EMAIL_CONFIRM)
    link = request.build_absolute_uri(reverse("approvals:confirm_email", args=[token.token]))
    try:
        send_mail(
            "Confirmez votre adresse email",
            f"Bonjour {user.first_name},\n\n"
            f"Confirmez votre inscription en cliquant sur ce lien :\n{link}\n\n"
            "Ce lien expire dans 48 heures. Un administrateur activera ensuite votre compte "
            "avant que vous puissiez vous connecter.",
            None,
            [user.email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Échec d'envoi de l'email de confirmation d'inscription à %s", user.email)
        return False


def confirm_email(request, token):
    email_token = get_object_or_404(EmailToken, token=token, purpose=EmailToken.Purpose.EMAIL_CONFIRM)
    if not email_token.is_valid():
        messages.error(request, "Ce lien de confirmation n'est plus valide. Contactez un administrateur.")
        return redirect("login")

    email_token.mark_used()
    profile, _ = UserProfile.objects.get_or_create(user=email_token.user)
    profile.email_confirmed_at = timezone.now()
    profile.save(update_fields=["email_confirmed_at"])
    messages.success(
        request,
        "Adresse email confirmée. Un administrateur doit maintenant activer votre compte "
        "avant que vous puissiez vous connecter.",
    )
    return redirect("login")


def login_view(request):
    if request.user.is_authenticated:
        return redirect(reverse("approvals:dashboard"))

    if request.method == "POST":
        form = AuthenticationForm(request, data=request.POST)
        if form.is_valid():
            user = form.get_user()
            active_email = EmailSettings.get_active()
            if active_email and active_email.require_login_confirmation and user.email:
                if _send_login_confirmation_email(request, user):
                    return render(request, "approvals/login_confirmation_sent.html", {"email": user.email})
                # La double authentification par email est exigée : si l'email ne part pas,
                # on ne connecte surtout pas l'utilisateur en la contournant silencieusement.
                messages.error(
                    request,
                    "Impossible d'envoyer l'email de confirmation de connexion pour le moment "
                    "(problème de configuration du serveur de messagerie). Contacte un administrateur.",
                )
                return render(request, "registration/login.html", {"form": AuthenticationForm(request)})
            auth_login(request, user)
            return redirect(request.GET.get("next") or reverse("approvals:dashboard"))
    else:
        form = AuthenticationForm(request)
    return render(request, "registration/login.html", {"form": form})


def _send_login_confirmation_email(request, user):
    """Retourne False (sans lever d'exception) si l'envoi échoue."""
    token = EmailToken.objects.create(
        user=user, purpose=EmailToken.Purpose.LOGIN_CONFIRM, backend_path=user.backend
    )
    link = request.build_absolute_uri(reverse("approvals:confirm_login", args=[token.token]))
    try:
        send_mail(
            "Confirmez votre connexion",
            f"Bonjour {user.first_name or user.username},\n\n"
            f"Cliquez sur ce lien pour finaliser votre connexion (valable 15 minutes) :\n{link}",
            None,
            [user.email],
            fail_silently=False,
        )
        return True
    except Exception:
        logger.exception("Échec d'envoi de l'email de confirmation de connexion à %s", user.email)
        return False


def confirm_login(request, token):
    email_token = get_object_or_404(EmailToken, token=token, purpose=EmailToken.Purpose.LOGIN_CONFIRM)
    if not email_token.is_valid():
        messages.error(request, "Ce lien de connexion a expiré. Reconnecte-toi.")
        return redirect("login")

    email_token.mark_used()
    user = email_token.user
    user.backend = email_token.backend_path or "django.contrib.auth.backends.ModelBackend"
    auth_login(request, user)
    messages.success(request, "Connexion confirmée.")
    return redirect("approvals:dashboard")
