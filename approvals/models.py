import secrets
import uuid

from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import FileExtensionValidator, MaxValueValidator, MinValueValidator, RegexValidator
from django.db import models

from . import crypto
from .validators import (
    validate_approvers_config,
    validate_criteria,
    validate_entity_name,
    validate_form_schema,
)

#fonction pour générer les tokens secrets

def _generate_token():
    return secrets.token_urlsafe(32)


# taille maximum d'une photo de profil

PROFILE_PHOTO_MAX_SIZE_MB = 2


#fonction pour réguler la limite d'upload de l'image de la photo de profil

def validate_profile_photo_size(file):
    if file.size > PROFILE_PHOTO_MAX_SIZE_MB * 1024 * 1024:
        raise ValidationError(f"La photo ne doit pas dépasser {PROFILE_PHOTO_MAX_SIZE_MB} Mo.")


class Department(models.Model):
    """Département de l'entreprise (retour client : afficher un nom, pas un ID
    brut). Sert de référentiel pour UserProfile.department et les critères
    de règles (ApprovalRule.criteria.department_ids, qui restent une liste
    de PK de Department)."""

    name = models.CharField(max_length=100, unique=True, validators=[validate_entity_name])

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Site(models.Model):
    """Site géographique de l'entreprise (même logique que Department)."""

    name = models.CharField(max_length=100, unique=True, validators=[validate_entity_name])

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


class Role(models.Model):
    """Rôle métier librement défini par un admin fonctionnel (ex: "Comptable",
    "RH", "Support technique") — purement descriptif, indépendant des
    permissions réelles de l'utilisateur (retour client : distinct du rôle
    système calculé automatiquement, voir system_role_label ci-dessous,
    affiché en lecture seule à côté de celui-ci)."""

    name = models.CharField(max_length=100, unique=True, validators=[validate_entity_name])

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return self.name


def system_role_label(user):
    """Rôle calculé à partir des permissions réelles et de la structure
    organisationnelle (pas un champ stocké) — utilisé côté client
    (context_processors.sidebar) et côté admin (UserAdmin/UserProfileAdmin),
    pour rester la même source de vérité aux deux endroits (retour client :
    affiché côté profil utilisateur mais absent côté admin)."""
    if user.is_superuser:
        return "Super admin"
    if user.is_staff:
        return "Admin fonctionnel"
    if user.direct_reports.exists():
        return "Manager"
    return "Demandeur"


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
    department = models.ForeignKey(
        Department, on_delete=models.SET_NULL, null=True, blank=True, related_name="members",
        help_text="Utilisé par les critères de règles (ApprovalRule.criteria.department_ids). "
        "À renseigner manuellement par un admin fonctionnel : l'annuaire AD ne fournit qu'un nom "
        "de département (department_name), pas d'identifiant stable.",
    )
    site = models.ForeignKey(Site, on_delete=models.SET_NULL, null=True, blank=True, related_name="members")
    role = models.ForeignKey(
        Role, on_delete=models.SET_NULL, null=True, blank=True, related_name="members",
        help_text="Rôle métier librement défini (ex: \"Comptable\") — purement descriptif, "
        "distinct du rôle système affiché juste à côté (Super admin/Admin fonctionnel/"
        "Manager/Demandeur, calculé à partir des permissions réelles).",
    )
    country_code = models.CharField(max_length=2, null=True, blank=True)

    # Champs informatifs synchronisés depuis Active Directory à la connexion
    # (voir approvals/auth_backends.py). Non utilisés par le moteur de routage
    # (qui s'appuie sur department/site, configurés par un admin fonctionnel) :
    # ils servent de repère pour faire ce mapping, pas de source de vérité pour les règles.
    department_name = models.CharField(max_length=100, null=True, blank=True)
    site_name = models.CharField(max_length=100, null=True, blank=True)
    last_ad_sync = models.DateTimeField(null=True, blank=True)

    # Inscription en ligne (voir approvals/auth_views.py). L'email confirmé
    # est nécessaire mais pas suffisant pour se connecter : l'activation du
    # compte (et l'assignation d'un rôle/manager/département) reste une
    # action distincte réservée à un admin fonctionnel.
    email_confirmed_at = models.DateTimeField(null=True, blank=True)

    # Stockée sur disque (MEDIA_ROOT) : la base ne garde que le chemin du fichier.
    photo = models.ImageField(
        upload_to="profile_photos/", null=True, blank=True,
        validators=[FileExtensionValidator(["jpg", "jpeg", "png"]), validate_profile_photo_size],
        help_text="JPG ou PNG, 2 Mo maximum.",
    )

    # Mis à jour par TrackLastSeenMiddleware à chaque requête authentifiée
    # (throttlé, voir le middleware) — sert à proposer un approbateur de
    # secours "actif aujourd'hui" quand un demandeur n'a personne pour
    # approuver ses demandes (retour client : nouvel employé sans manager,
    # et tous les admins/managers/délégués potentiels sont absents en même
    # temps — personne ne peut lui assigner un manager dans l'admin).
    last_seen_at = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"Profil de {self.user}"


class RequestType(models.Model):
    """Catégorie de demande et structure de son formulaire dynamique."""

    name = models.CharField(max_length=100, unique=True, validators=[validate_entity_name])
    code = models.CharField(max_length=20, unique=True)
    is_active = models.BooleanField(default=True)
    form_schema = models.JSONField(default=dict, validators=[validate_form_schema])
    schema_version = models.IntegerField(default=1)
    default_currency = models.CharField(
        max_length=10, blank=True, default="",
        help_text="Devise affichée pour les montants de ce type de demande (ex: EUR, USD, CAD). "
        "Laisser vide si ce type ne comporte pas de montant.",
    )
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
    last_reference_number = models.PositiveIntegerField(
        default=0,
        editable=False,
        help_text="Compteur interne utilisé pour générer la référence (ex: EXPENSE-000001) "
        "de chaque nouvelle demande de ce type — ne pas modifier manuellement.",
    )
    reference_form_pdf = models.FileField(
        upload_to="request_type_forms/%Y/%m/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["pdf"])],
        help_text="Document PDF expliquant ce qui est attendu pour ce type de demande "
        "(ex: le formulaire papier existant). Affiché au demandeur à côté du formulaire "
        "en ligne, et à l'approbateur lors de la révision — à titre de référence, les "
        "champs ci-dessus restent la source des données enregistrées.",
    )

    def __str__(self):
        return self.name


_HEX_COLOR_VALIDATOR = RegexValidator(r"^#[0-9A-Fa-f]{6}$", "Format attendu : #RRGGBB (ex: #1F3A5F).")


class DocumentBranding(models.Model):
    """Habillage du PDF résumé (voir approvals/pdf_export.py) : en-tête,
    pied de page — propre à chaque type de demande, plutôt qu'un habillage
    unique global, pour coller à l'en-tête réel des formulaires papier
    existants (ex: numéro de document, mentions spécifiques à un service)."""

    request_type = models.OneToOneField(
        RequestType, on_delete=models.CASCADE, related_name="branding"
    )
    header_text = models.TextField(
        blank=True,
        help_text="Affiché en haut du PDF, sous les logos (ex: adresse de l'entreprise, "
        "numéro de document, date de révision) — mise en forme simple possible "
        "(gras/italique/souligné/alignement).",
    )
    footer_text = models.TextField(
        blank=True,
        help_text="Affiché en bas de chaque page du PDF (ex: mentions légales) — mise en "
        "forme simple possible (gras/italique/souligné/alignement).",
    )
    footer_font_size = models.PositiveIntegerField(
        default=8,
        validators=[MinValueValidator(6), MaxValueValidator(14)],
        help_text="Taille de police (points) du pied de page.",
    )
    footer_color = models.CharField(
        max_length=7, blank=True, validators=[_HEX_COLOR_VALIDATOR],
        help_text="Couleur du pied de page (format #RRGGBB). Laisser vide pour un gris neutre.",
    )
    footer_image = models.ImageField(
        upload_to="branding_footer/%Y/%m/",
        blank=True,
        null=True,
        validators=[FileExtensionValidator(["png", "jpg", "jpeg"])],
        help_text="Image ou icône affichée en bas à gauche de chaque page (ex: sceau, "
        "certification) — optionnel.",
    )
    accent_color = models.CharField(
        max_length=7,
        blank=True,
        validators=[_HEX_COLOR_VALIDATOR],
        help_text="Couleur des titres de section et des champs mis en évidence (format #RRGGBB). "
        "Laisser vide pour la couleur par défaut.",
    )
    draft_color = models.CharField(
        max_length=7, blank=True, validators=[_HEX_COLOR_VALIDATOR],
        help_text="Couleur du statut \"Brouillon\" dans le PDF. Laisser vide pour la couleur par défaut.",
    )
    pending_color = models.CharField(
        max_length=7, blank=True, validators=[_HEX_COLOR_VALIDATOR],
        help_text="Couleur du statut \"En attente\" dans le PDF. Laisser vide pour la couleur par défaut.",
    )
    approved_color = models.CharField(
        max_length=7, blank=True, validators=[_HEX_COLOR_VALIDATOR],
        help_text="Couleur du statut \"Approuvée\" dans le PDF. Laisser vide pour la couleur par défaut.",
    )
    rejected_color = models.CharField(
        max_length=7, blank=True, validators=[_HEX_COLOR_VALIDATOR],
        help_text="Couleur du statut \"Refusée\" dans le PDF. Laisser vide pour la couleur par défaut.",
    )
    returned_color = models.CharField(
        max_length=7, blank=True, validators=[_HEX_COLOR_VALIDATOR],
        help_text="Couleur du statut \"Retournée\" dans le PDF. Laisser vide pour la couleur par défaut.",
    )
    body_font = models.CharField(
        max_length=100,
        blank=True,
        help_text="Police du corps du document — Helvetica, Times, Courier, ou le nom exact "
        "d'une police personnalisée (voir Polices personnalisées). Laisser vide pour Helvetica.",
    )
    body_font_size = models.PositiveIntegerField(
        default=11,
        validators=[MinValueValidator(6), MaxValueValidator(20)],
        help_text="Taille de police (points) des valeurs des champs — titres, labels et titres "
        "de section restent proportionnels à cette taille.",
    )

    class LineSpacing(models.TextChoices):
        COMPACT = "compact", "Compact"
        NORMAL = "normal", "Normal"
        SPACIOUS = "spacious", "Aéré"

    line_spacing = models.CharField(
        max_length=10, choices=LineSpacing.choices, default=LineSpacing.NORMAL,
        help_text="Espacement entre les lignes et les paragraphes du PDF.",
    )
    underline_values = models.BooleanField(
        default=False,
        help_text="Affiche un trait fin sous chaque valeur courte, comme un formulaire papier "
        "(ex: \"Nom : ______________\").",
    )

    class Meta:
        verbose_name = "Habillage de document"
        verbose_name_plural = "Habillages de document"

    def __str__(self):
        return f"Habillage — {self.request_type.name}"


def _validate_logo_extension(value):
    FileExtensionValidator(["png", "jpg", "jpeg"])(value)


class BrandingLogo(models.Model):
    """Un logo parmi plusieurs possibles pour un même habillage (retour
    client : logos multiples, ex: logo de l'entreprise + logo d'une
    certification) — affichés côte à côte en en-tête du PDF, dans l'ordre."""

    branding = models.ForeignKey(DocumentBranding, on_delete=models.CASCADE, related_name="logos")
    image = models.ImageField(upload_to="branding_logos/%Y/%m/", validators=[_validate_logo_extension])
    order = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["order", "id"]

    def __str__(self):
        return f"Logo #{self.order} — {self.branding.request_type.name}"


def _validate_ttf_extension(value):
    FileExtensionValidator(["ttf"])(value)


class CustomFont(models.Model):
    """Police personnalisée (ex: la police officielle de l'entreprise),
    utilisable dans l'éditeur visuel de mise en page (voir DocumentTemplate)
    — réutilisable sur plusieurs types de demande, comme les logos. Seul
    le fichier "normal" est obligatoire ; les variantes gras/italique sont
    utilisées si fournies, sinon fpdf2 simule un style approché à partir
    du fichier normal."""

    name = models.CharField(max_length=100, unique=True, validators=[validate_entity_name])
    regular_ttf = models.FileField(upload_to="custom_fonts/", validators=[_validate_ttf_extension])
    bold_ttf = models.FileField(
        upload_to="custom_fonts/", blank=True, null=True, validators=[_validate_ttf_extension]
    )
    italic_ttf = models.FileField(
        upload_to="custom_fonts/", blank=True, null=True, validators=[_validate_ttf_extension]
    )
    bold_italic_ttf = models.FileField(
        upload_to="custom_fonts/", blank=True, null=True, validators=[_validate_ttf_extension]
    )

    class Meta:
        verbose_name = "Police personnalisée"
        verbose_name_plural = "Polices personnalisées"

    def __str__(self):
        return self.name


#Document A4
DOCUMENT_TEMPLATE_PAGE_WIDTH_MM = 210  # A4
DOCUMENT_TEMPLATE_PAGE_HEIGHT_MM = 297


class DocumentTemplate(models.Model):
    """Mise en page du PDF dessinée librement par un admin (éditeur visuel,
    voir approvals/templates/approvals/document_template_editor.html et
    approvals/pdf_export.py) — remplace le rendu automatique (sections +
    DocumentBranding) pour ce type de demande quand elle existe. Format A4
    fixe pour l'instant (pas de configuration par type de demande)."""

    request_type = models.OneToOneField(
        RequestType, on_delete=models.CASCADE, related_name="document_template"
    )
    canvas_json = models.JSONField(
        default=dict, blank=True,
        help_text="Représentation Fabric.js de la mise en page — ne pas modifier manuellement, "
        "généré par l'éditeur visuel.",
    )

    class Meta:
        verbose_name = "Mise en page personnalisée"
        verbose_name_plural = "Mises en page personnalisées"

    def __str__(self):
        return f"Mise en page — {self.request_type.name}"


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
        # self.level peut être None ici (champ laissé vide dans le formulaire) :
        # Model.full_clean() appelle clean() même si clean_fields() a déjà
        # relevé une erreur "ce champ est obligatoire" sur level, pour que les
        # deux erreurs remontent ensemble (comportement Django documenté) —
        # comparer None à un entier plantait avec un TypeError non intercepté
        # (500 brut) au lieu de laisser passer le message d'erreur normal.
        if self.level is not None and self.level < 1:
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
        chevauchent : la plus spécifique gagne."""
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
    reference = models.CharField(
        max_length=30,
        unique=True,
        editable=False,
        blank=True,
        help_text="Code séquentiel lisible (ex: EXPENSE-000042), attribué automatiquement "
        "à la création — sert à distinguer deux demandes du même type dans les listes, "
        "l'UUID n'étant pas lisible.",
    )
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

    def save(self, *args, **kwargs):
        if self._state.adding and not self.reference:
            self.reference = self._next_reference()
        super().save(*args, **kwargs)

    def _next_reference(self):
        """Génère EXPENSE-000042, etc. L'UPDATE ... SET = F() + 1 est une seule
        instruction SQL atomique : sous SQLite les écritures sont sérialisées au
        niveau du fichier, donc deux demandes créées en même temps ne peuvent
        pas recevoir le même numéro, même sans verrou explicite en Python."""
        RequestType.objects.filter(pk=self.request_type_id).update(
            last_reference_number=models.F("last_reference_number") + 1
        )
        self.request_type.refresh_from_db(fields=["last_reference_number"])
        return f"{self.request_type.code}-{self.request_type.last_reference_number:06d}"

    def __str__(self):
        return f"{self.reference or self.id} ({self.status})"


ATTACHMENT_MAX_SIZE_MB = 5
ATTACHMENT_ALLOWED_EXTENSIONS = ["jpg", "jpeg", "png", "pdf"]


def validate_attachment_size(file):
    if file.size > ATTACHMENT_MAX_SIZE_MB * 1024 * 1024:
        raise ValidationError(f"Le fichier ne doit pas dépasser {ATTACHMENT_MAX_SIZE_MB} Mo.")


class RequestAttachment(models.Model):
    """Pièce jointe libre sur une demande —
    disponible pour tous les types de demande, sans configuration par
    l'admin (retour client : pas seulement pour les congés)."""

    request = models.ForeignKey(Request, on_delete=models.CASCADE, related_name="attachments")
    file = models.FileField(
        upload_to="request_attachments/%Y/%m/",
        validators=[FileExtensionValidator(ATTACHMENT_ALLOWED_EXTENSIONS), validate_attachment_size],
    )
    uploaded_by = models.ForeignKey(
        settings.AUTH_USER_MODEL, on_delete=models.SET_NULL, null=True, blank=True
    )
    uploaded_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["uploaded_at"]

    def filename(self):
        return self.file.name.rsplit("/", 1)[-1]

    def __str__(self):
        return self.filename()


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

    label = models.CharField(
        max_length=100, validators=[validate_entity_name],
        help_text='Ex: "Gmail (test)", "Exchange (production)".',
    )
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
        Purpose.LOGIN_CONFIRM: 60 * 15,        # 15 min pour un lien de connexion sur l'application
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


