from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied, ValidationError
from django.core.paginator import Paginator
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import reports as reports_module
from .forms import PersonalInfoForm, ProfilePhotoForm, build_dynamic_form, labeled_data
from .models import Request, RequestAttachment, RequestType, UserProfile
from .services import RoutingError, WorkflowEngine

LIST_PAGE_SIZE = 15


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
            try:
                engine.submit(actor=request.user)
            except RoutingError as exc:
                messages.error(request, str(exc))
                return render(
                    request, "approvals/request_form.html",
                    {"request_type": request_type, "form": form},
                )
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
            try:
                if is_draft:
                    engine.submit(actor=request.user)
                else:
                    engine.resubmit(actor=request.user)
            except RoutingError as exc:
                messages.error(request, str(exc))
                return render(request, "approvals/request_form.html", {**template_context, "form": form})
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
    dans l'historique, cf. Manuel d'Administration §6 sur la traçabilité)."""
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


@login_required
def my_requests(request):
    requests = Request.objects.filter(requester=request.user).select_related("request_type")
    type_code = request.GET.get("type")
    active_request_type = None
    if type_code:
        requests = requests.filter(request_type__code=type_code)
        active_request_type = RequestType.objects.filter(code=type_code).first()
    page_obj = Paginator(requests, LIST_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request, "approvals/my_requests.html",
        {
            "requests": page_obj, "page_obj": page_obj,
            "active_type": type_code, "active_request_type": active_request_type,
        },
    )


@login_required
def pending_approvals(request):
    pending = _pending_requests_for_user(request.user)
    type_code = request.GET.get("type")
    if type_code:
        pending = [req for req in pending if req.request_type.code == type_code]
    page_obj = Paginator(pending, LIST_PAGE_SIZE).get_page(request.GET.get("page"))
    return render(
        request, "approvals/pending_list.html",
        {"requests": page_obj, "page_obj": page_obj, "active_type": type_code},
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
            "data_rows": labeled_data(req.request_type, req.data),
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
