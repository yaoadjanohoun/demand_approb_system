import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models

from . import crypto
from .validators import (
    validate_approvers_config,
    validate_criteria,
    validate_form_schema,
)


def _generate_token():
    return secrets.token_urlsafe(32)


class UserProfile(models.Model):
    """Données organisationnelles minimales nécessaires au moteur de routage
    (manager, département, site). En production, ces données proviendront
    d'Active Directory ; ce modèle sert de source locale en attendant l'intégration LDAP.
    """

    user = models.OneToOneField(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="profile"
    )
    manager = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="direct_reports",
    )
    department_id = models.IntegerField(
        null=True, blank=True,
        help_text="ID numérique utilisé par les critères de règles (ApprovalRule.criteria.department_ids). "
        "À renseigner manuellement par un admin fonctionnel : l'annuaire AD ne fournit qu'un nom "
        "de département (department_name), pas d'identifiant numérique stable.",
    )
    site_id = models.IntegerField(null=True, blank=True)
    country_code = models.CharField(max_length=2, null=True, blank=True)

    # Champs informatifs synchronisés depuis Active Directory à la connexion
    # (voir approvals/auth_backends.py). Non utilisés par le moteur de routage
    # (qui s'appuie sur department_id/site_id, configurés par un admin fonctionnel) :
    # ils servent de repère pour faire ce mapping, pas de source de vérité pour les règles.
    department_name = models.CharField(max_length=100, null=True, blank=True)
    site_name = models.CharField(max_length=100, null=True, blank=True)
    last_ad_sync = models.DateTimeField(null=True, blank=True)

    # Inscription en ligne (voir approvals/auth_views.py). L'email confirmé
    # est nécessaire mais pas suffisant pour se connecter : l'activation du
    # compte (et l'assignation d'un rôle/manager/département) reste une
    # action distincte réservée à un admin fonctionnel.
    email_confirmed_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Profil de {self.user}"


class RequestType(models.Model):
    """Catégorie de demande et structure de son formulaire dynamique."""

    name = models.CharField(max_length=100, unique=True)
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)
    form_schema = models.JSONField(default=dict, validators=[validate_form_schema])
    schema_version = models.IntegerField(default=1)
    resume_on_resubmit = models.BooleanField(
        default=False,
        help_text="Si activé, une demande retournée puis resoumise reprend au niveau bloqué "
        "au lieu de redémarrer au niveau 1.",
    )
    is_sensitive = models.BooleanField(
        default=False,
        help_text="Marque les demandes de ce type comme sensibles (ex: congé médical).",
    )
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return self.name


class ApprovalRule(models.Model):
    """QUI approuve QUOI, sous QUELLES conditions, à quel niveau."""

    request_type = models.ForeignKey(
        RequestType, on_delete=models.CASCADE, related_name="approval_rules"
    )
    level = models.PositiveIntegerField(help_text="1 = premier niveau (Manager), 2 = Directeur, etc.")
    is_active = models.BooleanField(default=True)
    criteria = models.JSONField(
        default=dict, blank=True, validators=[validate_criteria]
    )
    approvers_config = models.JSONField(default=dict, validators=[validate_approvers_config])
    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["request_type", "level"]

    def clean(self):
        if self.level < 1:
            raise ValidationError({"level": "Le niveau doit être supérieur à 0."})

        if self.is_active and self.request_type_id:
            duplicate = (
                ApprovalRule.objects.filter(
                    request_type_id=self.request_type_id, level=self.level, is_active=True
                )
                .exclude(pk=self.pk)
                .filter(criteria=self.criteria)
                .exists()
            )
            if duplicate:
                raise ValidationError(
                    "Une autre règle active existe déjà pour ce type de demande, ce niveau "
                    "et exactement les mêmes conditions. Modifiez les conditions de l'une des "
                    "deux règles ou désactivez-en une."
                )

    def specificity(self):
        """Nombre de conditions de la règle. Sert à départager les règles qui se
        chevauchent (cf. Manuel d'Administration §4.3) : la plus spécifique gagne."""
        return len(self.criteria or {})

    def is_default(self):
        return not self.criteria

    def overlapping_rules(self):
        """Autres règles actives du même type/niveau dont les conditions ont la
        même spécificité — signale une ambiguïté que l'admin doit trancher."""
        if not self.request_type_id:
            return ApprovalRule.objects.none()
        candidates = ApprovalRule.objects.filter(
            request_type_id=self.request_type_id, level=self.level, is_active=True
        ).exclude(pk=self.pk)
        return [r for r in candidates if r.specificity() == self.specificity()]

    def __str__(self):
        return f"{self.request_type.code} - niveau {self.level}"


class Request(models.Model):
    """Instance concrète d'une demande soumise par un utilisateur."""

    class Status(models.TextChoices):
        DRAFT = "DRAFT", "Brouillon"
        PENDING = "PENDING", "En attente"
        APPROVED = "APPROVED", "Approuvée"
        REJECTED = "REJECTED", "Refusée"
        RETURNED = "RETURNED", "Retournée"

    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)
    request_type = models.ForeignKey(
        RequestType, on_delete=models.PROTECT, related_name="requests"
    )
    requester = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="requests"
    )
    status = models.CharField(
        max_length=10, choices=Status.choices, default=Status.DRAFT
    )
    current_level = models.PositiveIntegerField(default=1)
    data = models.JSONField(default=dict, blank=True)
    snapshot_metadata = models.JSONField(default=dict, blank=True, null=True)
    submitted_at = models.DateTimeField(null=True, blank=True)
    completed_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-created_at"]

    def clean(self):
        """Vérifie que `data` respecte le form_schema de son RequestType."""
        schema_fields = self.request_type.form_schema.get("fields", [])
        required_names = {f["name"] for f in schema_fields if f.get("required")}
        allowed_names = {f["name"] for f in schema_fields}

        missing = required_names - self.data.keys()
        if missing:
            raise ValidationError(
                {"data": f"Champs obligatoires manquants : {', '.join(sorted(missing))}"}
            )

        unknown = self.data.keys() - allowed_names
        if unknown:
            raise ValidationError(
                {"data": f"Champs inconnus pour ce type de demande : {', '.join(sorted(unknown))}"}
            )

    def __str__(self):
        return f"{self.request_type.code} #{self.id} ({self.status})"


class Delegation(models.Model):
    """Remplacement temporaire d'un approbateur absent."""

    delegator = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="delegations_given"
    )
    delegate = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="delegations_received"
    )
    start_date = models.DateField()
    end_date = models.DateField()
    scope = models.JSONField(
        default=dict,
        blank=True,
        null=True,
        help_text='Limite la délégation à certains types de demande. Ex: {"request_type_ids": [1, 5]}. '
        "Vide = délégation totale.",
    )

    class Meta:
        ordering = ["-start_date"]

    def clean(self):
        if self.start_date and self.end_date and self.start_date > self.end_date:
            raise ValidationError({"end_date": "La date de fin doit être postérieure à la date de début."})
        if self.delegator_id and self.delegate_id and self.delegator_id == self.delegate_id:
            raise ValidationError("Un utilisateur ne peut pas être son propre remplaçant.")

    @property
    def is_active(self):
        from django.utils import timezone

        today = timezone.localdate()
        return self.start_date <= today <= self.end_date

    def covers_request_type(self, request_type_id):
        if not self.scope or "request_type_ids" not in self.scope:
            return True
        return request_type_id in self.scope["request_type_ids"]

    def __str__(self):
        return f"{self.delegator} -> {self.delegate} ({self.start_date} - {self.end_date})"


class ApprovalLog(models.Model):
    """Journal immuable de toutes les actions et changements."""

    class ActionType(models.TextChoices):
        SUBMIT = "SUBMIT", "Soumission"
        APPROVE = "APPROVE", "Approbation"
        REJECT = "REJECT", "Refus"
        RETURN = "RETURN", "Retour"
        RULE_CHANGE = "RULE_CHANGE", "Modification de règle"
        DELEGATION_TRIGGERED = "DELEGATION_TRIGGERED", "Délégation déclenchée"
        FORCE_ADVANCE = "FORCE_ADVANCE", "Passage forcé (intervention admin)"
        REASSIGN = "REASSIGN", "Réassignation (intervention admin)"

    id = models.BigAutoField(primary_key=True)
    request = models.ForeignKey(
        Request, on_delete=models.CASCADE, related_name="logs", null=True, blank=True
    )
    actor = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    action_type = models.CharField(max_length=25, choices=ActionType.choices)
    comment = models.TextField(null=True, blank=True)
    previous_status = models.CharField(max_length=10, null=True, blank=True)
    new_status = models.CharField(max_length=10, null=True, blank=True)
    context = models.JSONField(default=dict, blank=True, null=True)
    timestamp = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-timestamp"]

    def __str__(self):
        return f"{self.get_action_type_display()} - {self.timestamp:%Y-%m-%d %H:%M}"


class EmailSettings(models.Model):
    """Configuration SMTP administrable sans toucher au code (retour client :
    "on va utiliser gmail pour les tests après nous aurons à le changer, pas
    directement dans le code mais directement dans l'espace admin"). Une seule
    ligne active à la fois ; DBEmailBackend (approvals/email_backend.py) l'utilise
    pour l'envoi. Si aucune n'est active, les emails partent sur la console
    (mode dégradé sûr pour le développement local).
    """

    label = models.CharField(max_length=100, help_text='Ex: "Gmail (test)", "Exchange (production)".')
    is_active = models.BooleanField(
        default=False,
        help_text="Une seule configuration active à la fois (la dernière activée gagne).",
    )
    host = models.CharField(max_length=255)
    port = models.PositiveIntegerField(default=587)
    username = models.CharField(max_length=255, blank=True)
    _password_encrypted = models.TextField(db_column="password_encrypted", blank=True)
    use_tls = models.BooleanField(default=True)
    from_email = models.EmailField(help_text="Adresse affichée comme expéditeur.")
    require_login_confirmation = models.BooleanField(
        default=True,
        help_text="Si activé, la connexion nécessite de cliquer un lien reçu par email "
        "avant d'être effective (double authentification par email).",
    )

    class Meta:
        verbose_name = "Configuration email"
        verbose_name_plural = "Configuration email"

    @property
    def password(self):
        return crypto.decrypt(self._password_encrypted)

    @password.setter
    def password(self, value):
        self._password_encrypted = crypto.encrypt(value)

    def save(self, *args, **kwargs):
        if self.is_active:
            EmailSettings.objects.exclude(pk=self.pk).update(is_active=False)
        super().save(*args, **kwargs)

    @classmethod
    def get_active(cls):
        return cls.objects.filter(is_active=True).first()

    def __str__(self):
        return f"{self.label} ({'active' if self.is_active else 'inactive'})"


class EmailToken(models.Model):
    """Jeton à usage unique envoyé par email : confirmation d'inscription ou
    validation de connexion (double authentification par email)."""

    class Purpose(models.TextChoices):
        EMAIL_CONFIRM = "EMAIL_CONFIRM", "Confirmation d'inscription"
        LOGIN_CONFIRM = "LOGIN_CONFIRM", "Confirmation de connexion"

    user = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.CASCADE, related_name="email_tokens")
    purpose = models.CharField(max_length=20, choices=Purpose.choices)
    token = models.CharField(max_length=64, unique=True, default=_generate_token)
    # Pour LOGIN_CONFIRM : quel backend (AD ou local) a authentifié l'utilisateur
    # au moment de la saisie du mot de passe, pour finaliser login() avec le même
    # backend une fois le lien cliqué (Django l'exige).
    backend_path = models.CharField(max_length=200, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    used_at = models.DateTimeField(null=True, blank=True)

    LIFETIMES = {
        Purpose.EMAIL_CONFIRM: 60 * 60 * 48,   # 48h pour confirmer une inscription
        Purpose.LOGIN_CONFIRM: 60 * 15,        # 15 min pour un lien de connexion
    }

    def is_valid(self):
        from django.utils import timezone

        if self.used_at is not None:
            return False
        age = (timezone.now() - self.created_at).total_seconds()
        return age <= self.LIFETIMES[self.purpose]

    def mark_used(self):
        from django.utils import timezone

        self.used_at = timezone.now()
        self.save(update_fields=["used_at"])

    def __str__(self):
        return f"{self.get_purpose_display()} - {self.user}"
