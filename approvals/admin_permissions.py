"""Callbacks de permission pour la navigation latérale de l'admin (Unfold).

Retour client : un utilisateur (ex: membre du groupe "Comité de direction")
à qui on a donné une permission mais pas une autre voyait quand même TOUS
les liens de la sidebar, cliquait sur celui qu'il n'avait pas le droit
d'utiliser, et tombait sur un 403 Forbidden brut. Chaque item de navigation
est maintenant filtré par la permission Django correspondante : un lien
n'apparaît que si l'utilisateur peut réellement l'utiliser."""


def can_view_requesttype(request):
    return request.user.has_perm("approvals.view_requesttype")


def can_view_approvalrule(request):
    return request.user.has_perm("approvals.view_approvalrule")


def can_view_delegation(request):
    return request.user.has_perm("approvals.view_delegation")


def can_view_request(request):
    return request.user.has_perm("approvals.view_request")


def can_view_approvallog(request):
    return request.user.has_perm("approvals.view_approvallog")


def can_view_reports(request):
    # Page de rapports maison (pas un ModelAdmin) : ouverte à tout le staff,
    # comme le décorateur @staff_member_required qui protège déjà la vue.
    return request.user.is_staff


def can_view_userprofile(request):
    return request.user.has_perm("approvals.view_userprofile")


def can_view_department(request):
    return request.user.has_perm("approvals.view_department")


def can_view_site(request):
    return request.user.has_perm("approvals.view_site")


def can_view_user(request):
    return request.user.has_perm("auth.view_user")


def can_view_group(request):
    return request.user.has_perm("auth.view_group")


def can_view_emailsettings(request):
    return request.user.has_perm("approvals.view_emailsettings")
