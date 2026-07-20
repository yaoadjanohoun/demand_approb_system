from django import forms
from django.contrib import admin, messages
from django.contrib.auth.models import User
from django.db import models
from django.shortcuts import redirect
from django.urls import reverse
from django_json_widget.widgets import JSONEditorWidget
from unfold.admin import ModelAdmin
from unfold.decorators import action, display
from unfold.forms import BaseDialogForm
from unfold.widgets import UnfoldAdminSelectWidget, UnfoldAdminTextareaWidget

from .models import ApprovalLog, ApprovalRule, Delegation, Request, RequestType, UserProfile
from .services import RoutingError, WorkflowEngine
from .widgets import ApproversConfigBuilderWidget, CriteriaBuilderWidget, FormSchemaBuilderWidget

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
            kwargs["widget"] = self.field_widgets[db_field.name]
        elif isinstance(db_field, models.JSONField):
            kwargs["widget"] = JSONEditorWidget
        return super().formfield_for_dbfield(db_field, request, **kwargs)


#création du profil admin
@admin.register(UserProfile)
class UserProfileAdmin(ModelAdmin):
    list_display = ("user", "manager", "department_id", "site_id", "country_code")
    search_fields = ("user__username",)
    autocomplete_fields = ("user", "manager")


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
    list_display = ("name", "code", "is_active", "schema_version", "resume_on_resubmit", "is_sensitive")
    list_filter = ("is_active", "is_sensitive")
    search_fields = ("name", "code")
    inlines = [ApprovalRuleInline]
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
            "Options avancées",
            {"fields": ("resume_on_resubmit", "is_sensitive")},
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


# creation de modèle des règles d'approbation d'un compte admin
@admin.register(ApprovalRule)
class ApprovalRuleAdmin(NamedFieldWidgetMixin, ModelAdmin):
    field_widgets = {"criteria": CriteriaBuilderWidget, "approvers_config": ApproversConfigBuilderWidget}
    list_display = ("request_type", "level", "is_active", "created_by", "updated_at")
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


#creation de modeme des requetes d'admin
@admin.register(Request)
class RequestAdmin(JSONWidgetMixin, ModelAdmin):
    list_display = ("id", "request_type", "requester", "status_display", "current_level", "submitted_at")
    list_filter = ("status", "request_type")
    search_fields = ("id", "requester__username")
    # Lecture seule : modifier une demande ici contournerait le moteur de routage
    # (pas de recalcul du niveau, pas d'entrée dans ApprovalLog). Les décisions
    # se prennent depuis l'interface de soumission/approbation ou le bouton "Intervenir".
    readonly_fields = [f.name for f in Request._meta.fields]
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


#creation de modèle de création de délégation par un admin.
@admin.register(Delegation)
class DelegationAdmin(ModelAdmin):
    list_display = ("delegator", "delegate", "start_date", "end_date", "is_active_display", "scope")
    list_filter = ("start_date", "end_date")
    autocomplete_fields = ("delegator", "delegate")
    formfield_overrides = {models.JSONField: {"widget": JSONEditorWidget}}

    @display(description="Active", boolean=True)
    def is_active_display(self, obj):
        return obj.is_active


#cration de modele pour consulter les logs d'approbation admin
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
