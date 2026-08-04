from django import forms
from django.contrib import admin, messages
from django.contrib.auth.admin import GroupAdmin as BaseGroupAdmin, UserAdmin as BaseUserAdmin
from django.contrib.auth.models import Group, User
from django.db import models
from django.shortcuts import redirect
from django.urls import reverse
from django.utils.html import format_html
from django_json_widget.widgets import JSONEditorWidget
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.forms import AdminPasswordChangeForm, BaseDialogForm, UserChangeForm, UserCreationForm
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextareaWidget

from .models import (
    ApprovalLog, ApprovalRule, BrandingLogo, CustomFont, Delegation, Department, DocumentBranding,
    EmailSettings, Request, RequestAttachment, RequestType, Role, Site, UserProfile, system_role_label,
)
from .services import RoutingError, WorkflowEngine
from .validators import validate_entity_name, validate_person_name
from .widgets import (
    ApproversConfigBuilderWidget, CriteriaBuilderWidget, FormSchemaBuilderWidget, RichTextWidget,
)


admin.site.site_header = "Système de Demandes et d'Approbation — Administration"
admin.site.site_title = "Système de Demandes et d'Approbation"
admin.site.index_title = "Tableau de bord de l'Administration"

# django.contrib.auth enregistre User/Group avec le ModelAdmin Django brut, qui
# n'a pas les attributs propres à Unfold (ex: show_add_link) : le bouton
# "Ajouter" et une partie du chrome restaient alors invisibles même pour un
# superuser, sans lien avec les permissions (bug relevé en revue client).
# On les ré-enregistre avec le ModelAdmin d'Unfold, en gardant la logique
# métier (fieldsets, formulaires) de Django.
admin.site.unregister(Group)
admin.site.unregister(User)


class GroupAdminForm(forms.ModelForm):
    # django.contrib.auth.models.Group.name n'a par défaut aucune restriction
    # de caractères (même trou que User.first_name/last_name, voir
    # ValidatedUserChangeForm plus bas).
    name = forms.CharField(max_length=150, validators=[validate_entity_name])

    class Meta:
        model = Group
        fields = "__all__"


@admin.register(Group)
class GroupAdmin(BaseGroupAdmin, ModelAdmin):
    form = GroupAdminForm


class _ValidatedNameFormMixin:
    """Ajoute validate_person_name à first_name/last_name sans toucher aux
    widgets d'Unfold — Django's User.first_name/last_name n'a par défaut
    aucune restriction de caractères (retour déploiement : un "Prénom"
    enregistré via l'admin avec une suite de "/" cassait l'affichage du nom
    complet partout dans l'app)."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        for field_name in ("first_name", "last_name"):
            if field_name in self.fields:
                self.fields[field_name].validators.append(validate_person_name)


class ValidatedUserChangeForm(_ValidatedNameFormMixin, UserChangeForm):
    pass


class ValidatedUserCreationForm(_ValidatedNameFormMixin, UserCreationForm):
    pass


@admin.register(User)
class UserAdmin(BaseUserAdmin, ModelAdmin):
    form = ValidatedUserChangeForm
    add_form = ValidatedUserCreationForm
    change_password_form = AdminPasswordChangeForm
    # Retour client : le rôle (calculé, ET le rôle métier personnalisé) est
    # affiché côté profil utilisateur mais n'apparaissait nulle part côté
    # admin — ajoutés à la liste "Utilisateurs" pour rester visibles sans
    # avoir à ouvrir chaque profil individuellement.
    list_display = (
        "username", "email", "first_name", "last_name", "is_staff",
        "systeme_role_display", "role_display",
    )

    @display(description="Rôle système")
    def systeme_role_display(self, obj):
        return system_role_label(obj)

    @display(description="Rôle")
    def role_display(self, obj):
        profile = getattr(obj, "profile", None)
        return profile.role if profile and profile.role_id else "—"

STATUS_LABELS = {
    "Brouillon": "bg-gray-100 text-gray-700 dark:bg-gray-700 dark:text-gray-100",
    "En attente": "bg-yellow-100 text-yellow-700 dark:bg-yellow-900 dark:text-yellow-200",
    "Approuvée": "bg-green-100 text-green-700 dark:bg-green-900 dark:text-green-200",
    "Refusée": "bg-red-100 text-red-700 dark:bg-red-900 dark:text-red-200",
    "Retournée": "bg-blue-100 text-blue-700 dark:bg-blue-900 dark:text-blue-200",
}


class JSONWidgetMixin:
    """Remplace le textarea JSON brut par un éditeur visuel (arbre + JSON)."""

    formfield_overrides = {
        models.JSONField: {"widget": JSONEditorWidget},
    }


class NamedFieldWidgetMixin:
    """Comme JSONWidgetMixin, mais permet d'attribuer un widget différent à
    des champs JSON précis (ex: constructeur visuel pour form_schema/criteria,
    éditeur JSON générique pour les autres). À définir sur la sous-classe :
    field_widgets = {"nom_du_champ": MaWidgetClass}.
    """

    field_widgets = {}

    def formfield_for_dbfield(self, db_field, request, **kwargs):
        if db_field.name in self.field_widgets:
            widget_cls = self.field_widgets[db_field.name]
            try:
                # Certains widgets (ex: ApproversConfigBuilderWidget) ont besoin de la
                # requête pour scoper leurs choix à l'admin connecté ; les autres l'ignorent.
                kwargs["widget"] = widget_cls(request=request)
            except TypeError:
                kwargs["widget"] = widget_cls
        elif isinstance(db_field, models.JSONField):
            kwargs["widget"] = JSONEditorWidget
        return super().formfield_for_dbfield(db_field, request, **kwargs)


@admin.register(Department)
class DepartmentAdmin(ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Site)
class SiteAdmin(ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


@admin.register(Role)
class RoleAdmin(ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


#création du profil admin
@admin.register(UserProfile)
class UserProfileAdmin(ModelAdmin):
    list_display = (
        "user", "compte_actif_display", "email_confirmee_display", "manager",
        "department", "department_name", "site", "site_name", "role", "systeme_role_display",
        "country_code",
    )
    list_filter = ("department", "site", "role")
    search_fields = ("user__username", "department__name", "site__name", "role__name")
    autocomplete_fields = ("user", "manager")
    readonly_fields = ("department_name", "site_name", "last_ad_sync", "email_confirmed_at", "systeme_role_display")
    actions = ["activer_les_comptes"]
    fieldsets = (
        (None, {"fields": ("user", "manager", "country_code")}),
        (
            "Rôle",
            {
                "fields": ("role", "systeme_role_display"),
                "description": "\"Rôle\" est librement défini par un admin fonctionnel (ex: "
                "\"Comptable\") — purement descriptif. \"Rôle système\" est calculé à partir des "
                "permissions réelles (lecture seule, non modifiable ici).",
            },
        ),
        (
            "Utilisées par le moteur de routage",
            {
                "fields": ("department", "site"),
                "description": "Renseignées manuellement par un admin fonctionnel : "
                "l'annuaire AD ne fournit qu'un nom de département/site (ci-dessous), pas la "
                "référence stable qu'utilisent les règles.",
            },
        ),
        (
            "Synchronisées depuis Active Directory (lecture seule)",
            {"fields": ("department_name", "site_name", "last_ad_sync")},
        ),
        (
            "Inscription en ligne",
            {"fields": ("email_confirmed_at",)},
        ),
    )

    @display(description="Compte actif", boolean=True)
    def compte_actif_display(self, obj):
        return obj.user.is_active

    @display(description="Rôle système")
    def systeme_role_display(self, obj):
        return system_role_label(obj.user)

    @display(description="Email confirmé", boolean=True)
    def email_confirmee_display(self, obj):
        return obj.email_confirmed_at is not None

    @admin.action(description="Activer les comptes sélectionnés (email confirmé requis)")
    def activer_les_comptes(self, request, queryset):
        eligible = queryset.filter(email_confirmed_at__isnull=False, user__is_active=False)
        skipped = queryset.count() - eligible.count()
        activated = 0
        for profile in eligible.select_related("user"):
            profile.user.is_active = True
            profile.user.save(update_fields=["is_active"])
            activated += 1
        if activated:
            messages.success(request, f"{activated} compte(s) activé(s).")
        if skipped:
            messages.warning(
                request,
                f"{skipped} compte(s) ignoré(s) (déjà actif ou email pas encore confirmé).",
            )


# cration du modèle des paramètres mail
class EmailSettingsForm(forms.ModelForm):
    password = forms.CharField(
        label="Mot de passe / clé d'application",
        widget=forms.PasswordInput(render_value=True),
        required=False,
        help_text="Laisser vide pour conserver le mot de passe actuel.",
    )

    class Meta:
        model = EmailSettings
        fields = ["label", "is_active", "host", "port", "username", "use_tls", "from_email", "require_login_confirmation"]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        if self.instance.pk:
            self.fields["password"].initial = self.instance.password

    def save(self, commit=True):
        instance = super().save(commit=False)
        new_password = self.cleaned_data.get("password")
        if new_password:
            instance.password = new_password
        if commit:
            instance.save()
        return instance


@admin.register(EmailSettings)
class EmailSettingsAdmin(ModelAdmin):
    """Configuration SMTP (ex: Gmail en test, Exchange en production) —
    modifiable ici sans toucher au code (retour client). Réservé au super
    admin : ni le groupe "Admins fonctionnels" ni admin_fonctionnel n'ont de
    permission dessus par défaut (identifiants de messagerie sensibles)."""

    form = EmailSettingsForm
    list_display = ("label", "host", "port", "is_active", "require_login_confirmation")
    fieldsets = (
        (None, {"fields": ("label", "is_active")}),
        ("Serveur SMTP", {"fields": ("host", "port", "username", "password", "use_tls", "from_email")}),
        (
            "Sécurité",
            {
                "fields": ("require_login_confirmation",),
                "description": "Si activé, chaque connexion nécessite de cliquer un lien reçu par "
                "email (double authentification par email) avant d'être effective.",
            },
        ),
    )


class ApprovalRuleInline(NamedFieldWidgetMixin, admin.TabularInline):
    model = ApprovalRule
    # extra=1 : la ligne est pré-rendue au chargement de la page, ce qui est nécessaire
    # pour que les widgets s'initialisent (le constructeur visuel comme l'éditeur JSON ne
    # s'activent pas sur les lignes ajoutées dynamiquement via "Add another" dans un inline).
    # Pour ajouter d'autres règles au même type, utiliser la page "Règles d'approbation".
    extra = 1
    fields = ("level", "is_active", "criteria", "approvers_config", "created_by")
    field_widgets = {"criteria": CriteriaBuilderWidget, "approvers_config": ApproversConfigBuilderWidget}


#creation du modele de requete d'approbation d'un type admin
@admin.register(RequestType)
class RequestTypeAdmin(NamedFieldWidgetMixin, ModelAdmin):
    field_widgets = {"form_schema": FormSchemaBuilderWidget}
    list_display = (
        "name", "code", "is_active", "schema_version", "resume_on_resubmit",
        "is_sensitive", "default_rule_display", "template_editor_link",
    )
    list_filter = ("is_active", "is_sensitive")
    search_fields = ("name", "code")
    inlines = [ApprovalRuleInline]

    @display(description="Mise en page PDF")
    def template_editor_link(self, obj):
        url = reverse("approvals:document_template_editor", args=[obj.pk])
        return format_html('<a href="{}">Concevoir le PDF →</a>', url)

    @display(description="Règle par défaut (dernier niveau)", boolean=True)
    def default_rule_display(self, obj):
        """Signale l'absence de règle "par défaut" (sans condition) au dernier
        niveau d'approbation actif —  pour éviter que des demandes non couvertes par les règles
        spécifiques ne sautent silencieusement ce niveau."""
        active_rules = list(obj.approval_rules.filter(is_active=True))
        if not active_rules:
            return True  # rien à signaler : aucune règle configurée
        last_level = max(r.level for r in active_rules)
        return any(r.level == last_level and r.is_default() for r in active_rules)
    fieldsets = (
        ("Identification", {"fields": ("name", "code", "is_active")}),
        (
            "Formulaire de la demande",
            {
                "fields": ("form_schema", "schema_version"),
                "description": (
                    "Ajoutez les champs proposés au demandeur (nom technique, label, type, obligatoire). "
                    "Le nom technique n'accepte que des minuscules et underscores (ex: date_debut)."
                ),
            },
        ),
        (
            "Document de référence (PDF)",
            {
                "fields": ("reference_form_pdf",),
                "description": (
                    "PDF optionnel affiché au demandeur et à l'approbateur à titre de référence "
                    "(ex: le formulaire papier existant) — les champs configurés ci-dessus "
                    "restent la source des données enregistrées, ce PDF n'est pas rempli automatiquement."
                ),
            },
        ),
        (
            "Options avancées",
            {"fields": ("resume_on_resubmit", "is_sensitive", "default_currency")},
        ),
    )

    def save_formset(self, request, form, formset, change):
        instances = formset.save(commit=False)
        for obj in formset.deleted_objects:
            obj.delete()
        for obj in instances:
            if isinstance(obj, ApprovalRule) and not obj.created_by_id:
                obj.created_by = request.user
            obj.save()
        formset.save_m2m()


class BrandingLogoInline(admin.TabularInline):
    model = BrandingLogo
    extra = 1
    fields = ("image", "order")


@admin.register(DocumentBranding)
class DocumentBrandingAdmin(NamedFieldWidgetMixin, ModelAdmin):
    field_widgets = {"header_text": RichTextWidget, "footer_text": RichTextWidget}
    list_display = ("request_type",)
    inlines = [BrandingLogoInline]
    fieldsets = (
        ("Type de demande", {"fields": ("request_type",)}),
        (
            "En-tête / pied de page",
            {
                "fields": ("header_text", "footer_text"),
                "description": (
                    "Affichés sur le PDF résumé de chaque demande de ce type "
                    "(voir bouton \"Télécharger le PDF\" sur une demande) — les logos "
                    "s'ajoutent séparément ci-dessous."
                ),
            },
        ),
    )


@admin.register(CustomFont)
class CustomFontAdmin(ModelAdmin):
    list_display = ("name",)
    search_fields = ("name",)


# creation de modèle des règles d'approbation d'un compte admin
@admin.register(ApprovalRule)
class ApprovalRuleAdmin(NamedFieldWidgetMixin, ModelAdmin):
    field_widgets = {"criteria": CriteriaBuilderWidget, "approvers_config": ApproversConfigBuilderWidget}
    list_display = ("request_type", "level", "is_active", "conflict_display", "created_by", "updated_at")
    list_filter = ("request_type", "is_active", "level")
    autocomplete_fields = ("created_by",)
    fieldsets = (
        ("Portée de la règle", {"fields": ("request_type", "level", "is_active")}),
        (
            "Conditions de déclenchement (criteria)",
            {
                "fields": ("criteria",),
                "description": "Ajoutez une ou plusieurs conditions (toutes doivent être vraies). Aucune condition = règle par défaut, toujours applicable.",
            },
        ),
        (
            "Approbateur (approvers_config)",
            {
                "fields": ("approvers_config",),
                "description": "Choisissez qui doit approuver à ce niveau.",
            },
        ),
        ("Traçabilité", {"fields": ("created_by",)}),
    )

    @display(description="Conflit potentiel")
    def conflict_display(self, obj):
        if not obj.is_active:
            return "—"
        overlapping = obj.overlapping_rules()
        if not overlapping:
            return "—"
        return f"⚠ {len(overlapping)} règle(s) à la même spécificité"

    def save_model(self, request, obj, form, change):
        if not obj.created_by_id:
            obj.created_by = request.user
        super().save_model(request, obj, form, change)


class InterventionForm(BaseDialogForm):
    """Formulaire du bouton "Intervenir" (
    demande bloquée, ex. approbateur parti sans délégation)."""

    ACTION_CHOICES = [
        ("force_advance", "Forcer le passage au niveau suivant"),
        ("reassign", "Réassigner à un autre utilisateur"),
    ]

    action_type = forms.ChoiceField(
        choices=ACTION_CHOICES, label="Action", widget=UnfoldAdminSelectWidget
    )
    new_approver = forms.ModelChoiceField(
        queryset=User.objects.filter(is_active=True),
        required=False,
        label="Nouvel approbateur (si réassignation)",
        widget=UnfoldAdminSelectWidget,
    )
    comment = forms.CharField(
        label="Commentaire (obligatoire, journalisé)",
        widget=UnfoldAdminTextareaWidget,
    )

    def clean(self):
        cleaned = super().clean()
        if cleaned.get("action_type") == "reassign" and not cleaned.get("new_approver"):
            raise forms.ValidationError(
                "Sélectionnez un nouvel approbateur pour une réassignation."
            )
        return cleaned


class RequestAttachmentInline(admin.TabularInline):
    model = RequestAttachment
    extra = 0
    fields = ("file", "uploaded_by", "uploaded_at")
    readonly_fields = ("file", "uploaded_by", "uploaded_at")
    can_delete = False

    def has_add_permission(self, request, obj=None):
        return False


#creation de modeme des requetes d'admin
@admin.register(Request)
class RequestAdmin(JSONWidgetMixin, ModelAdmin):
    list_display = ("reference", "request_type", "requester", "status_display", "current_level", "submitted_at")
    list_filter = ("status", "request_type")
    search_fields = ("reference", "requester__username")
    # Lecture seule : modifier une demande ici contournerait le moteur de routage
    # (pas de recalcul du niveau, pas d'entrée dans ApprovalLog). Les décisions
    # se prennent depuis l'interface de soumission/approbation ou le bouton "Intervenir".
    readonly_fields = [f.name for f in Request._meta.fields]
    inlines = [RequestAttachmentInline]
    actions_detail = ["intervene"]

    @display(description="Statut", label=STATUS_LABELS)
    def status_display(self, obj):
        return obj.get_status_display()

    def has_add_permission(self, request):
        return False

    @action(
        description="Intervenir",
        url_path="intervenir",
        permissions=["change"],
        icon="build",
        dialog={
            "title": "Intervention administrative",
            "description": (
                "À utiliser uniquement en cas de blocage exceptionnel "
                "(ex: approbateur parti sans délégation active)."
            ),
            "form_class": InterventionForm,
            "form_submit_text": "Confirmer",
        },
    )
    def intervene(self, request, form, object_id=None):
        obj = self.get_object(request, object_id)
        change_url = reverse(
            f"admin:{self.model._meta.app_label}_{self.model._meta.model_name}_change",
            args=[object_id],
        )

        engine = WorkflowEngine(obj)
        data = form.cleaned_data
        try:
            if data["action_type"] == "force_advance":
                engine.force_advance(request.user, data["comment"])
            else:
                engine.reassign(request.user, [data["new_approver"].id], data["comment"])
            messages.success(request, "Intervention effectuée avec succès.")
        except RoutingError as exc:
            messages.error(request, str(exc))

        return redirect(change_url)


# création de délégation par un admin.
@admin.register(Delegation)
class DelegationAdmin(ModelAdmin):
    list_display = ("delegator", "delegate", "start_date", "end_date", "is_active_display", "scope")
    list_filter = ("start_date", "end_date")
    autocomplete_fields = ("delegator", "delegate")
    formfield_overrides = {models.JSONField: {"widget": JSONEditorWidget}}

    @display(description="Active", boolean=True)
    def is_active_display(self, obj):
        return obj.is_active


#creation de modele pour consulter les logs d'approbation admin
@admin.register(ApprovalLog)
class ApprovalLogAdmin(ModelAdmin):
    list_display = ("timestamp", "request", "actor", "action_type", "previous_status", "new_status")
    list_filter = ("action_type",)
    search_fields = ("request__id", "actor__username")
    readonly_fields = [f.name for f in ApprovalLog._meta.fields]

    def has_add_permission(self, request):
        return False

    def has_change_permission(self, request, obj=None):
        return False

    def has_delete_permission(self, request, obj=None):
        return False
