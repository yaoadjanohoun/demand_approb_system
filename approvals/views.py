from django.contrib import messages
from django.contrib.admin.views.decorators import staff_member_required
from django.contrib.auth.decorators import login_required
from django.core.exceptions import PermissionDenied
from django.shortcuts import get_object_or_404, redirect, render

from . import reports as reports_module
from .forms import build_dynamic_form
from .models import Request, RequestType
from .services import RoutingError, WorkflowEngine


@login_required
def dashboard(request):
    request_types = RequestType.objects.filter(is_active=True)
    return render(request, "approvals/dashboard.html", {"request_types": request_types})


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


@login_required
def my_requests(request):
    requests = Request.objects.filter(requester=request.user)
    return render(request, "approvals/my_requests.html", {"requests": requests})


@login_required
def pending_approvals(request):
    candidates = Request.objects.filter(status=Request.Status.PENDING)
    pending = [
        req for req in candidates
        if request.user.id in WorkflowEngine(req).get_effective_approvers()
    ]
    return render(request, "approvals/pending_list.html", {"requests": pending})


def _can_view(user, req):
    if req.requester_id == user.id:
        return True
    return user.id in WorkflowEngine(req).get_effective_approvers()


@login_required
def request_detail(request, pk):
    req = get_object_or_404(Request, pk=pk)
    if not (_can_view(request.user, req) or request.user.is_staff):
        raise PermissionDenied

    is_current_approver = (
        req.status == Request.Status.PENDING
        and request.user.id in WorkflowEngine(req).get_effective_approvers()
    )
    return render(
        request, "approvals/request_detail.html",
        {
            "req": req,
            "logs": req.logs.select_related("actor"),
            "is_current_approver": is_current_approver,
            "is_requester": req.requester_id == request.user.id,
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
