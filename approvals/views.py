import json

from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.contrib.auth import get_user_model
from django.core.exceptions import (
    PermissionDenied, RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent, ValidationError,
)
from django.core.paginator import Paginator
from django.db.models import Q
from django.http import HttpResponse, HttpResponseBadRequest
from django.shortcuts import get_object_or_404, redirect, render
from django.template import loader
from django.urls import reverse
from django.utils import timezone

from . import reports as reports_module
from .forms import PersonalInfoForm, ProfilePhotoForm, build_dynamic_form, grouped_labeled_data, labeled_data
from .models import (
    BrandingLogo, CustomFont, DOCUMENT_TEMPLATE_PAGE_HEIGHT_MM, DOCUMENT_TEMPLATE_PAGE_WIDTH_MM,
    DocumentTemplate, Request, RequestAttachment, RequestType, UserProfile,
)
from .pdf_export import generate_request_summary_pdf
from .services import RoutingError, WorkflowEngine

User = get_user_model()

LIST_PAGE_SIZE = 15


#dashboard côté utilisateur

@login_required
def dashboard(request):
    request_types = RequestType.objects.filter(is_active=True).order_by("name")
    my_requests_qs = Request.objects.filter(requester=request.user)
    stats = {
        "my_pending": my_requests_qs.filter(status=Request.Status.PENDING).count(),
        "my_total": my_requests_qs.count(),
        "to_approve": len(_pending_requests_for_user(request.user)),
    }
    return render(
        request, "approvals/dashboard.html",
        {"request_types": request_types, "stats": stats},
    )


#vue du profil utilisateur

@login_required
def profile(request):
    user_profile, _ = UserProfile.objects.get_or_create(user=request.user)
    action = request.POST.get("action") if request.method == "POST" else None

    if action == "remove_photo":
        user_profile.photo.delete(save=False)
        user_profile.photo = None
        user_profile.save()
        messages.success(request, "Photo de profil supprimée.")
        return redirect("approvals:profile")

    if action == "update_info":
        info_form = PersonalInfoForm(request.POST, instance=request.user)
        if info_form.is_valid():
            info_form.save()
            messages.success(request, "Informations personnelles mises à jour.")
            return redirect("approvals:profile")
    else:
        info_form = PersonalInfoForm(instance=request.user)

    if action == "update_photo":
        photo_form = ProfilePhotoForm(request.POST, request.FILES, instance=user_profile)
        if photo_form.is_valid():
            photo_form.save()
            messages.success(request, "Photo de profil mise à jour.")
            return redirect("approvals:profile")
    else:
        photo_form = ProfilePhotoForm(instance=user_profile)

    manager_display = "—"
    if user_profile.manager:
        manager_display = user_profile.manager.get_full_name() or user_profile.manager.username
    return render(
        request, "approvals/profile.html",
        {
            "profile": user_profile, "manager_display": manager_display,
            "photo_form": photo_form, "info_form": info_form,
        },
    )


def _approver_candidates():
    """Utilisateurs pouvant jouer le rôle d'approbateur de secours (retour
    client) : ceux qui ont déjà une forme d'autorité d'approbation dans le
    système — staff (admin fonctionnel), manager d'au moins un profil, ou
    délégué d'au moins une délégation. Un simple demandeur n'a pas ces
    droits et n'apparaît jamais dans cette liste.

    Triés par dernière navigation aujourd'hui (le plus récent en premier :
    "le plus actif"). Si personne n'a navigué aujourd'hui, on retombe sur
    la liste complète plutôt que de bloquer le demandeur — l'approbateur
    choisi verra la demande dès sa prochaine connexion, quel que soit le jour."""
    candidates = list(
        User.objects.filter(is_active=True)
        .filter(Q(is_staff=True) | Q(direct_reports__isnull=False) | Q(delegations_received__isnull=False))
        .distinct()
        .select_related("profile")
    )
    today = timezone.localdate()
    active_today = [
        u for u in candidates
        if getattr(u, "profile", None) and u.profile.last_seen_at and u.profile.last_seen_at.date() == today
    ]
    active_today.sort(key=lambda u: u.profile.last_seen_at, reverse=True)
    return active_today or candidates


def _submit_with_manager_fallback(request, engine, resubmit=False):
    """Tente la soumission ; si elle échoue précisément faute de manager
    configuré (WorkflowEngine.missing_manager_candidate), propose au
    demandeur de choisir lui-même un approbateur de secours parmi les
    utilisateurs actifs aujourd'hui — plutôt qu'un blocage total si personne
    (admin, directeur, délégué...) n'est disponible pour corriger son profil
    (retour client). Une fois choisi, il devient son manager permanent :
    plus besoin de refaire ce choix aux prochaines demandes.

    Retourne (succès: bool, contexte_supplémentaire: dict|None) — le contexte
    n'est renseigné que lorsqu'il faut afficher le sélecteur d'approbateur."""
    action = engine.resubmit if resubmit else engine.submit
    try:
        action(actor=request.user)
        return True, None
    except RoutingError as exc:
        if not engine.missing_manager_candidate():
            messages.error(request, str(exc))
            return False, None

        chosen_id = request.POST.get("chosen_approver_id")
        if chosen_id:
            chosen = _validate_chosen_approver(chosen_id)
            if chosen:
                profile, _ = UserProfile.objects.get_or_create(user=request.user)
                profile.manager = chosen
                profile.save(update_fields=["manager"])
                try:
                    action(actor=request.user)
                    messages.success(
                        request,
                        f"{chosen.get_full_name() or chosen.username} a été assigné comme ton manager "
                        "et recevra désormais tes demandes à approuver.",
                    )
                    return True, None
                except RoutingError as exc2:
                    messages.error(request, str(exc2))
                    return False, None

        messages.error(
            request,
            "Tu n'as pas encore d'approbateur assigné, et personne n'est disponible pour le "
            "faire à ta place pour le moment. Choisis-en un ci-dessous pour continuer.",
        )
        return False, {"needs_approver_pick": True, "approver_candidates": _approver_candidates()}


def _validate_chosen_approver(chosen_id):
    """Valide que l'ID choisi correspond bien à un candidat légitime — pas
    n'importe quel utilisateur (ex: un demandeur ne peut pas se choisir
    lui-même, ou choisir un compte désactivé)."""
    try:
        chosen_id = int(chosen_id)
    except (TypeError, ValueError):
        return None
    return next((u for u in _approver_candidates() if u.id == chosen_id), None)


#creer une demande de requete
@login_required
def request_create(request, type_id):
    request_type = get_object_or_404(RequestType, pk=type_id, is_active=True)

    if request.method == "POST":
        action = request.POST.get("action", "submit")
        form = build_dynamic_form(request_type, data=request.POST)
        attachment_files = request.FILES.getlist("attachments")

        if action == "draft":
            for field in form.fields.values():
                field.required = False
            if form.is_valid():
                new_request = Request(
                    request_type=request_type,
                    requester=request.user,
                    status=Request.Status.DRAFT,
                    data=_serialize_form_data(form),
                )
                try:
                    attachments = _build_attachments(new_request, attachment_files, request.user)
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                    return render(
                        request, "approvals/request_form.html",
                        {"request_type": request_type, "form": form},
                    )
                new_request.save()
                _save_attachments(attachments)
                messages.success(request, "Brouillon enregistré.")
                return redirect("approvals:request_edit", pk=new_request.pk)
        elif form.is_valid():
            new_request = Request(
                request_type=request_type,
                requester=request.user,
                data=_serialize_form_data(form),
            )
            try:
                attachments = _build_attachments(new_request, attachment_files, request.user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return render(
                    request, "approvals/request_form.html",
                    {"request_type": request_type, "form": form},
                )
            engine = WorkflowEngine(new_request)
            success, picker_context = _submit_with_manager_fallback(request, engine)
            if not success:
                context = {"request_type": request_type, "form": form}
                if picker_context:
                    context.update(picker_context)
                return render(request, "approvals/request_form.html", context)
            _save_attachments(attachments)
            messages.success(request, "Demande soumise avec succès.")
            return redirect("approvals:request_detail", pk=new_request.pk)
    else:
        form = build_dynamic_form(request_type)

    return render(
        request, "approvals/request_form.html",
        {"request_type": request_type, "form": form},
    )


@login_required
def request_edit(request, pk):
    """Permet au demandeur de continuer un brouillon, ou de corriger et
    resoumettre une demande RETOURNÉE."""
    req = get_object_or_404(Request, pk=pk)
    if req.requester_id != request.user.id:
        raise PermissionDenied
    if req.status not in (Request.Status.DRAFT, Request.Status.RETURNED):
        messages.error(request, "Cette demande ne peut pas être modifiée dans son état actuel.")
        return redirect("approvals:request_detail", pk=pk)

    is_draft = req.status == Request.Status.DRAFT
    template_context = {
        "request_type": req.request_type, "editing": True, "is_draft": is_draft,
        "existing_attachments": req.attachments.all(),
    }

    if request.method == "POST":
        action = request.POST.get("action", "submit")
        form = build_dynamic_form(req.request_type, data=request.POST)
        attachment_files = request.FILES.getlist("attachments")

        if is_draft and action == "draft":
            for field in form.fields.values():
                field.required = False
            if form.is_valid():
                try:
                    attachments = _build_attachments(req, attachment_files, request.user)
                except ValidationError as exc:
                    messages.error(request, " ".join(exc.messages))
                    return render(request, "approvals/request_form.html", {**template_context, "form": form})
                req.data = _serialize_form_data(form)
                req.save()
                _save_attachments(attachments)
                messages.success(request, "Brouillon enregistré.")
                return redirect("approvals:request_edit", pk=pk)
        elif form.is_valid():
            try:
                attachments = _build_attachments(req, attachment_files, request.user)
            except ValidationError as exc:
                messages.error(request, " ".join(exc.messages))
                return render(request, "approvals/request_form.html", {**template_context, "form": form})
            req.data = _serialize_form_data(form)
            req.save()
            engine = WorkflowEngine(req)
            success, picker_context = _submit_with_manager_fallback(request, engine, resubmit=not is_draft)
            if not success:
                context = {**template_context, "form": form}
                if picker_context:
                    context.update(picker_context)
                return render(request, "approvals/request_form.html", context)
            _save_attachments(attachments)
            messages.success(
                request, "Demande soumise avec succès." if is_draft else "Demande resoumise avec succès."
            )
            return redirect("approvals:request_detail", pk=pk)
    else:
        form = build_dynamic_form(req.request_type, initial=req.data)

    return render(request, "approvals/request_form.html", {**template_context, "form": form})


@login_required
def request_delete(request, pk):
    """Suppression réservée aux brouillons (une demande soumise doit rester
    dans l'historique)."""
    req = get_object_or_404(Request, pk=pk)
    if req.requester_id != request.user.id:
        raise PermissionDenied
    if req.status != Request.Status.DRAFT:
        messages.error(request, "Cette demande ne peut plus être supprimée (elle n'est plus à l'état brouillon).")
        return redirect("approvals:request_detail", pk=pk)
    if request.method == "POST":
        req.delete()
        messages.success(request, "Brouillon supprimé.")
    return redirect("approvals:my_requests")


def _serialize_form_data(form):
    data = {}
    for name, value in form.cleaned_data.items():
        if hasattr(value, "isoformat"):
            value = value.isoformat()
        else:
            from decimal import Decimal

            if isinstance(value, Decimal):
                value = float(value)
        data[name] = value
    return data


def _build_attachments(req, files, user):
    """Valide tous les fichiers avant d'en enregistrer un seul (tout ou rien) —
    évite de laisser une demande avec une pièce jointe manquante à cause d'un
    fichier invalide plus loin dans la sélection."""
    attachments = [RequestAttachment(request=req, file=f, uploaded_by=user) for f in files]
    for attachment in attachments:
        # "request" est exclu : la demande parente n'est pas encore enregistrée à ce
        # stade (validation "tout ou rien" avant toute écriture), donc la vérification
        # de clé étrangère de Django échouerait à tort.
        attachment.full_clean(exclude=["request"])
    return attachments


def _save_attachments(attachments):
    for attachment in attachments:
        attachment.save()


def _pending_requests_for_user(user):
    candidates = Request.objects.filter(status=Request.Status.PENDING).select_related("request_type", "requester")
    return [req for req in candidates if user.id in WorkflowEngine(req).get_effective_approvers()]


def _search_haystack(req, include_requester=False):
    """Texte dans lequel chercher pour une demande : le type, le statut, et
    chaque champ de son formulaire dynamique — via labeled_data, qui associe
    déjà chaque valeur à son label configuré par type de demande (retour
    client : les champs ne sont pas les mêmes d'un type à l'autre, ex:
    "fournisseur" pour Achat IT, "motif" pour Congés — la recherche doit
    porter sur ces libellés et valeurs, pas juste un texte générique)."""
    parts = [req.reference, req.request_type.name, req.get_status_display()]
    if include_requester:
        parts.append(req.requester.get_full_name() or req.requester.username)
    for row in labeled_data(req.request_type, req.data or {}):
        parts.append(str(row["label"]))
        parts.append(str(row["value"]))
    return " ".join(parts).lower()


def _filter_by_search(requests, query, include_requester=False):
    terms = query.lower().split()
    if not terms:
        return requests
    return [
        req for req in requests
        if all(term in _search_haystack(req, include_requester=include_requester) for term in terms)
    ]


def _attach_labeled_rows(requests):
    """Ajoute .labeled_rows (label -> valeur formatée, via labeled_data) à
    chaque demande — retour client : les listes "Mes demandes"/"À approuver"
    doivent afficher tous les attributs des demandes, y compris les champs
    propres au formulaire de chaque type (montant, motif, fournisseur...),
    différents d'un type à l'autre. Attaché ici plutôt que dans le template
    pour réutiliser labeled_data (déjà la source de vérité pour le détail
    d'une demande et pour la recherche) sans exposer sa signature dans les
    templates."""
    for req in requests:
        req.labeled_rows = labeled_data(req.request_type, req.data or {})
    return requests


@login_required
def my_requests(request):
    requests = Request.objects.filter(requester=request.user).select_related("request_type")
    type_code = request.GET.get("type")
    active_request_type = None
    if type_code:
        requests = requests.filter(request_type__code=type_code)
        active_request_type = RequestType.objects.filter(code=type_code).first()
    search_query = request.GET.get("q", "").strip()
    if search_query:
        requests = _filter_by_search(list(requests), search_query)
    page_obj = Paginator(requests, LIST_PAGE_SIZE).get_page(request.GET.get("page"))
    _attach_labeled_rows(page_obj)
    return render(
        request, "approvals/my_requests.html",
        {
            "requests": page_obj, "page_obj": page_obj,
            "active_type": type_code, "active_request_type": active_request_type,
            "search_query": search_query,
        },
    )


@login_required
def pending_approvals(request):
    pending = _pending_requests_for_user(request.user)
    type_code = request.GET.get("type")
    if type_code:
        pending = [req for req in pending if req.request_type.code == type_code]
    search_query = request.GET.get("q", "").strip()
    if search_query:
        pending = _filter_by_search(pending, search_query, include_requester=True)
    page_obj = Paginator(pending, LIST_PAGE_SIZE).get_page(request.GET.get("page"))
    _attach_labeled_rows(page_obj)
    return render(
        request, "approvals/pending_list.html",
        {"requests": page_obj, "page_obj": page_obj, "active_type": type_code, "search_query": search_query},
    )


def _can_view(user, req):
    if req.requester_id == user.id:
        return True
    return WorkflowEngine(req).is_or_was_approver(user.id)


@login_required
def request_detail(request, pk):
    req = get_object_or_404(Request, pk=pk)
    if not (_can_view(request.user, req) or request.user.is_staff):
        raise PermissionDenied

    is_current_approver = (
        req.status == Request.Status.PENDING
        and request.user.id in WorkflowEngine(req).get_effective_approvers()
    )
    is_requester = req.requester_id == request.user.id
    next_request = None
    if is_requester:
        back_url = reverse("approvals:my_requests")
        back_label = "Mes demandes"
    else:
        back_url = reverse("approvals:pending_approvals")
        back_label = "À approuver"
        remaining = [
            other for other in _pending_requests_for_user(request.user)
            if other.request_type_id == req.request_type_id and other.id != req.id
        ]
        next_request = remaining[0] if remaining else None
    return render(
        request, "approvals/request_detail.html",
        {
            "req": req,
            "data_groups": grouped_labeled_data(req.request_type, req.data),
            "logs": req.logs.select_related("actor"),
            "attachments": req.attachments.all(),
            "is_current_approver": is_current_approver,
            "is_requester": is_requester,
            "next_request": next_request,
            "back_url": back_url,
            "back_label": back_label,
        },
    )


@login_required
def request_download_pdf(request, pk):
    """PDF résumé téléchargeable des réponses saisies (retour client) — en
    plus du document de référence (RequestType.reference_form_pdf), généré
    à la volée à partir des mêmes données que la page de détail, pas une
    copie remplie du PDF de référence."""
    req = get_object_or_404(Request, pk=pk)
    if not (_can_view(request.user, req) or request.user.is_staff):
        raise PermissionDenied

    pdf_bytes = generate_request_summary_pdf(req)
    response = HttpResponse(pdf_bytes, content_type="application/pdf")
    response["Content-Disposition"] = f'attachment; filename="{req.reference}.pdf"'
    return response


@login_required
def request_approve(request, pk):
    req = get_object_or_404(Request, pk=pk)
    if request.method == "POST":
        engine = WorkflowEngine(req)
        try:
            engine.approve(request.user, request.POST.get("comment", ""))
            messages.success(request, "Demande approuvée.")
        except RoutingError as exc:
            messages.error(request, str(exc))
    return redirect("approvals:request_detail", pk=pk)


@login_required
def request_reject(request, pk):
    req = get_object_or_404(Request, pk=pk)
    if request.method == "POST":
        engine = WorkflowEngine(req)
        try:
            engine.reject(request.user, request.POST.get("comment", ""))
            messages.success(request, "Demande refusée.")
        except RoutingError as exc:
            messages.error(request, str(exc))
    return redirect("approvals:request_detail", pk=pk)


@login_required
def request_return(request, pk):
    req = get_object_or_404(Request, pk=pk)
    if request.method == "POST":
        engine = WorkflowEngine(req)
        try:
            engine.return_for_info(request.user, request.POST.get("comment", ""))
            messages.success(request, "Demande retournée au demandeur.")
        except RoutingError as exc:
            messages.error(request, str(exc))
    return redirect("approvals:request_detail", pk=pk)


@staff_member_required
def reports(request):
    context = {
        "summary": reports_module.summary_stats(),
        "volume": reports_module.volume_by_month(),
        "rejection": reports_module.rejection_rate_by_type(),
        "duration_by_type": reports_module.average_approval_time_by_type(),
        "duration_by_department": reports_module.average_approval_time_by_department(),
    }
    return render(request, "approvals/reports.html", context)


@staff_member_required
def reports_export(request):
    return reports_module.export_requests_csv()


@staff_member_required
def document_template_editor(request, type_id):
    """Éditeur visuel (Fabric.js) de la mise en page du PDF pour un type de
    demande — voir DocumentTemplate. Remplace le rendu automatique pour ce
    type quand une mise en page y est enregistrée."""
    request_type = get_object_or_404(RequestType, pk=type_id)
    template, _ = DocumentTemplate.objects.get_or_create(request_type=request_type)

    if request.method == "POST":
        try:
            canvas_json = json.loads(request.POST.get("canvas_json", "{}"))
        except ValueError:
            return HttpResponseBadRequest("JSON invalide.")
        template.canvas_json = canvas_json
        template.save()
        messages.success(request, "Mise en page enregistrée.")
        return redirect("approvals:document_template_editor", type_id=type_id)

    logos = BrandingLogo.objects.filter(branding__request_type=request_type)
    return render(
        request, "approvals/document_template_editor.html",
        {
            "request_type": request_type,
            "template": template,
            "fields": request_type.form_schema.get("fields", []),
            "fonts": CustomFont.objects.all(),
            "logos": logos,
            "page_width_mm": DOCUMENT_TEMPLATE_PAGE_WIDTH_MM,
            "page_height_mm": DOCUMENT_TEMPLATE_PAGE_HEIGHT_MM,
        },
    )


# Django n'affiche par défaut aucun détail sur un 400 (page brute "Bad
# Request (400)", volontairement — voir django/views/defaults.py) : un
# fichier joint trop volumineux (au-delà de DJANGO_MAX_UPLOAD_MB, settings.py)
# atterrissait ici sans aucun message compréhensible pour l'utilisateur
# (retour déploiement, confondu avec une erreur serveur). On distingue ce cas
# précis pour un message actionnable ; le reste garde un message générique
# (jamais le détail de l'exception, qui peut révéler des infos internes).
def handler400(request, exception=None):
    if isinstance(exception, (RequestDataTooBig, TooManyFieldsSent, TooManyFilesSent)):
        message = "Le fichier envoyé est trop volumineux. Réduis sa taille et réessaie."
    else:
        message = "La requête n'a pas pu être traitée. Réessaie, ou contacte un administrateur si le problème persiste."
    html = loader.render_to_string("400.html", {"message": message}, request=request)
    return HttpResponseBadRequest(html)
