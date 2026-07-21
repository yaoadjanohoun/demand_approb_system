from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render
from django.urls import reverse

from . import reports as reports_module
from .forms import ProfilePhotoForm, build_dynamic_form, labeled_data
from .models import Request, RequestType, UserProfile
from .services import RoutingError, WorkflowEngine


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

    if request.method == "POST" and request.POST.get("action") == "remove_photo":
        user_profile.photo.delete(save=False)
        user_profile.photo = None
        user_profile.save()
        messages.success(request, "Photo de profil supprimée.")
        return redirect("approvals:profile")

    if request.method == "POST":
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
        {"profile": user_profile, "manager_display": manager_display, "photo_form": photo_form},
    )


@login_required
def request_create(request, type_id):
    request_type = get_object_or_404(RequestType, pk=type_id, is_active=True)

    if request.method == "POST":
        form = build_dynamic_form(request_type, data=request.POST)
        if form.is_valid():
            new_request = Request(
                request_type=request_type,
                requester=request.user,
                data=_serialize_form_data(form),
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
    """Permet au demandeur de corriger et resoumettre une demande RETOURNÉE."""
    req = get_object_or_404(Request, pk=pk)
    if req.requester_id != request.user.id:
        raise PermissionDenied
    if req.status != Request.Status.RETURNED:
        messages.error(request, "Cette demande ne peut pas être modifiée dans son état actuel.")
        return redirect("approvals:request_detail", pk=pk)

    if request.method == "POST":
        form = build_dynamic_form(req.request_type, data=request.POST)
        if form.is_valid():
            req.data = _serialize_form_data(form)
            req.save()
            engine = WorkflowEngine(req)
            try:
                engine.resubmit(actor=request.user)
            except RoutingError as exc:
                messages.error(request, str(exc))
                return render(
                    request, "approvals/request_form.html",
                    {"request_type": req.request_type, "form": form, "editing": True},
                )
            messages.success(request, "Demande resoumise avec succès.")
            return redirect("approvals:request_detail", pk=pk)
    else:
        form = build_dynamic_form(req.request_type, initial=req.data)

    return render(
        request, "approvals/request_form.html",
        {"request_type": req.request_type, "form": form, "editing": True},
    )


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


def _pending_requests_for_user(user):
    candidates = Request.objects.filter(status=Request.Status.PENDING).select_related("request_type", "requester")
    return [req for req in candidates if user.id in WorkflowEngine(req).get_effective_approvers()]


@login_required
def my_requests(request):
    requests = Request.objects.filter(requester=request.user).select_related("request_type")
    type_code = request.GET.get("type")
    if type_code:
        requests = requests.filter(request_type__code=type_code)
    return render(
        request, "approvals/my_requests.html",
        {"requests": requests, "active_type": type_code},
    )


@login_required
def pending_approvals(request):
    pending = _pending_requests_for_user(request.user)
    type_code = request.GET.get("type")
    if type_code:
        pending = [req for req in pending if req.request_type.code == type_code]
    return render(
        request, "approvals/pending_list.html",
        {"requests": pending, "active_type": type_code},
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
    if is_requester:
        back_url = reverse("approvals:my_requests")
        back_label = "Mes demandes"
    else:
        back_url = reverse("approvals:pending_approvals")
        back_label = "À approuver"
    return render(
        request, "approvals/request_detail.html",
        {
            "req": req,
            "data_rows": labeled_data(req.request_type, req.data),
            "logs": req.logs.select_related("actor"),
            "is_current_approver": is_current_approver,
            "is_requester": is_requester,
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
        "volume": reports_module.volume_by_month(),
        "rejection": reports_module.rejection_rate_by_type(),
        "duration_by_type": reports_module.average_approval_time_by_type(),
        "duration_by_department": reports_module.average_approval_time_by_department(),
    }
    return render(request, "approvals/reports.html", context)


@staff_member_required
def reports_export(request):
    return reports_module.export_requests_csv()
