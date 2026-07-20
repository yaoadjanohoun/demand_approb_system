"""Moteur de routage des demandes (voir "Diagrammes de Flux.txt").

- À la soumission, le moteur évalue les ApprovalRule actives et fige la liste
  nominale des approbateurs de CHAQUE niveau dans Request.snapshot_metadata.
  Une modification ultérieure des règles n'affecte plus cette demande.
- Les délégations (absences), elles, sont résolues au moment où un niveau
  devient courant — pas à la soumission — car une absence est par nature
  datée et peut être déclarée après coup.
"""
from django.contrib.auth.models import Group
from django.utils import timezone

from .models import ApprovalLog, ApprovalRule, Delegation, Request


class RoutingError(Exception):
    """Erreur métier du moteur de routage (règle invalide, action non autorisée, etc.)."""


class WorkflowEngine:
    def __init__(self, request: Request):
        self.request = request

    # ------------------------------------------------------------------
    # Soumission
    # ------------------------------------------------------------------
    def submit(self, actor=None):
        request = self.request
        if request.status != Request.Status.DRAFT:
            raise RoutingError("Seule une demande en brouillon peut être soumise.")

        request.full_clean()  # valide `data` contre le form_schema du RequestType
        snapshot = self._build_snapshot()
        previous_status = request.status

        if not snapshot:
            # Aucune règle ne correspond : approbation automatique (cf. diagramme, noeud G)
            request.status = Request.Status.APPROVED
            request.snapshot_metadata = {"workflow_snapshot": []}
            request.submitted_at = timezone.now()
            request.completed_at = timezone.now()
            request.save()
            self._log(actor, ApprovalLog.ActionType.SUBMIT, previous_status, request.status)
            return request

        request.status = Request.Status.PENDING
        request.current_level = snapshot[0]["level"]
        request.snapshot_metadata = {"workflow_snapshot": snapshot}
        request.submitted_at = timezone.now()
        request.save()
        self._log(actor, ApprovalLog.ActionType.SUBMIT, previous_status, request.status)
        self._activate_level(request.current_level)
        return request

    def _build_snapshot(self):
        rules = ApprovalRule.objects.filter(
            request_type_id=self.request.request_type_id, is_active=True
        )
        candidates_by_level = {}
        for rule in rules:
            if self._matches(rule.criteria):
                candidates_by_level.setdefault(rule.level, []).append(rule)

        snapshot = []
        for level in sorted(candidates_by_level):
            # Règle la plus spécifique = celle avec le plus de critères
            best_rule = max(candidates_by_level[level], key=lambda r: len(r.criteria))
            snapshot.append(
                {
                    "level": level,
                    "rule_id": best_rule.id,
                    "approver_ids": self._resolve_approvers(best_rule.approvers_config),
                    "status": "waiting",
                }
            )
        return snapshot

    def _matches(self, criteria):
        if not criteria:
            return True  # règle "par défaut" sans critères

        profile = getattr(self.request.requester, "profile", None)

        if "min_amount" in criteria or "max_amount" in criteria:
            amount = self._get_amount()
            if amount is None:
                return False
            if "min_amount" in criteria and amount < criteria["min_amount"]:
                return False
            if "max_amount" in criteria and amount > criteria["max_amount"]:
                return False

        if "department_ids" in criteria:
            if not profile or profile.department_id not in criteria["department_ids"]:
                return False

        if "site_id" in criteria:
            if not profile or profile.site_id != criteria["site_id"]:
                return False

        if "country_code" in criteria:
            if not profile or profile.country_code != criteria["country_code"]:
                return False

        return True

    def _get_amount(self):
        data = self.request.data or {}
        for key in ("montant", "amount"):
            if key in data:
                try:
                    return float(data[key])
                except (TypeError, ValueError):
                    return None
        return None

    def _resolve_approvers(self, config):
        approver_type = config.get("type")

        if approver_type == "user":
            user_id = config.get("user_id")
            return [user_id] if user_id else []

        if approver_type == "group":
            group_id = config.get("group_id")
            if not group_id:
                return []
            return list(
                Group.objects.get(pk=group_id)
                .user_set.filter(is_active=True)
                .values_list("id", flat=True)
            )

        if approver_type == "manager":
            profile = getattr(self.request.requester, "profile", None)
            if profile and profile.manager_id:
                return [profile.manager_id]
            if config.get("fallback_user_id"):
                return [config["fallback_user_id"]]
            return []

        raise RoutingError(
            f"Type d'approbateur '{approver_type}' non pris en charge pour le moment "
            "(role/custom nécessitent un modèle d'organisation, phase future)."
        )

    # ------------------------------------------------------------------
    # Activation de niveau / délégation
    # ------------------------------------------------------------------
    def _find_entry(self, level):
        for entry in self.request.snapshot_metadata.get("workflow_snapshot", []):
            if entry["level"] == level:
                return entry
        return None

    def _current_entry(self):
        return self._find_entry(self.request.current_level)

    def _activate_level(self, level):
        """Marque le niveau comme courant et résout les délégations en direct."""
        entry = self._find_entry(level)
        if entry is None:
            return
        entry["status"] = "pending"
        today = timezone.localdate()
        active_ids = []
        for user_id in entry["approver_ids"]:
            delegation = (
                Delegation.objects.filter(
                    delegator_id=user_id, start_date__lte=today, end_date__gte=today
                )
                .order_by("-start_date")
                .first()
            )
            if delegation and delegation.covers_request_type(self.request.request_type_id):
                active_ids.append(delegation.delegate_id)
                self._log(
                    None,
                    ApprovalLog.ActionType.DELEGATION_TRIGGERED,
                    None,
                    None,
                    context={
                        "level": level,
                        "delegator_id": user_id,
                        "delegate_id": delegation.delegate_id,
                        "delegation_id": delegation.id,
                    },
                )
            else:
                active_ids.append(user_id)
        entry["active_approver_ids"] = active_ids
        self.request.save()

    def _is_effective_approver(self, user, entry):
        return user is not None and user.id in entry.get("active_approver_ids", entry["approver_ids"])

    def get_effective_approvers(self):
        entry = self._current_entry()
        if not entry:
            return []
        return entry.get("active_approver_ids", entry["approver_ids"])

    # ------------------------------------------------------------------
    # Décisions
    # ------------------------------------------------------------------
    def approve(self, actor, comment=""):
        request = self.request
        if request.status != Request.Status.PENDING:
            raise RoutingError("La demande n'est pas en attente d'approbation.")
        entry = self._current_entry()
        if entry is None or not self._is_effective_approver(actor, entry):
            raise RoutingError("Vous n'êtes pas approbateur du niveau courant de cette demande.")

        entry["status"] = "approved"
        previous_status = request.status
        next_entry = self._find_entry(request.current_level + 1)

        if next_entry:
            request.current_level += 1
            request.save()
            self._log(
                actor, ApprovalLog.ActionType.APPROVE, previous_status, request.status,
                context={"level": request.current_level - 1},
            )
            self._activate_level(request.current_level)
        else:
            request.status = Request.Status.APPROVED
            request.completed_at = timezone.now()
            request.save()
            self._log(
                actor, ApprovalLog.ActionType.APPROVE, previous_status, request.status,
                context={"level": request.current_level},
            )
        return request

    def reject(self, actor, comment):
        if not comment:
            raise RoutingError("Un motif est obligatoire pour refuser une demande.")
        request = self.request
        if request.status != Request.Status.PENDING:
            raise RoutingError("La demande n'est pas en attente d'approbation.")
        entry = self._current_entry()
        if entry is None or not self._is_effective_approver(actor, entry):
            raise RoutingError("Vous n'êtes pas approbateur du niveau courant de cette demande.")

        entry["status"] = "rejected"
        previous_status = request.status
        request.status = Request.Status.REJECTED
        request.completed_at = timezone.now()
        request.save()
        self._log(actor, ApprovalLog.ActionType.REJECT, previous_status, request.status, comment=comment)
        return request

    def return_for_info(self, actor, comment):
        if not comment:
            raise RoutingError("Un commentaire est obligatoire pour retourner une demande.")
        request = self.request
        if request.status != Request.Status.PENDING:
            raise RoutingError("La demande n'est pas en attente d'approbation.")
        entry = self._current_entry()
        if entry is None or not self._is_effective_approver(actor, entry):
            raise RoutingError("Vous n'êtes pas approbateur du niveau courant de cette demande.")

        entry["status"] = "returned"
        previous_status = request.status
        request.status = Request.Status.RETURNED
        request.save()
        self._log(actor, ApprovalLog.ActionType.RETURN, previous_status, request.status, comment=comment)
        return request

    # ------------------------------------------------------------------
    # Intervention administrative (cf. Manuel d'Administration §6.1 : demande
    # bloquée car l'approbateur a quitté l'entreprise sans délégation, etc.)
    # ------------------------------------------------------------------
    def force_advance(self, actor, comment):
        """Force le passage au niveau suivant sans validation individuelle."""
        if not comment:
            raise RoutingError("Un commentaire est obligatoire pour une intervention administrative.")
        request = self.request
        if request.status != Request.Status.PENDING:
            raise RoutingError("Seule une demande en attente peut faire l'objet d'une intervention.")
        entry = self._current_entry()
        if entry is None:
            raise RoutingError("Aucun niveau courant trouvé pour cette demande.")

        entry["status"] = "forced"
        previous_status = request.status
        next_entry = self._find_entry(request.current_level + 1)

        if next_entry:
            request.current_level += 1
            request.save()
            self._log(
                actor, ApprovalLog.ActionType.FORCE_ADVANCE, previous_status, request.status,
                comment=comment, context={"level": request.current_level - 1},
            )
            self._activate_level(request.current_level)
        else:
            request.status = Request.Status.APPROVED
            request.completed_at = timezone.now()
            request.save()
            self._log(
                actor, ApprovalLog.ActionType.FORCE_ADVANCE, previous_status, request.status,
                comment=comment, context={"level": request.current_level},
            )
        return request

    def reassign(self, actor, new_approver_ids, comment):
        """Réassigne manuellement le niveau courant à d'autres utilisateurs."""
        if not comment:
            raise RoutingError("Un commentaire est obligatoire pour une réassignation.")
        if not new_approver_ids:
            raise RoutingError("Sélectionnez au moins un nouvel approbateur.")
        request = self.request
        if request.status != Request.Status.PENDING:
            raise RoutingError("Seule une demande en attente peut être réassignée.")
        entry = self._current_entry()
        if entry is None:
            raise RoutingError("Aucun niveau courant trouvé pour cette demande.")

        previous_ids = entry.get("active_approver_ids", entry["approver_ids"])
        entry["active_approver_ids"] = list(new_approver_ids)
        request.save()
        self._log(
            actor, ApprovalLog.ActionType.REASSIGN, request.status, request.status,
            comment=comment,
            context={
                "level": request.current_level,
                "previous_approver_ids": previous_ids,
                "new_approver_ids": list(new_approver_ids),
            },
        )
        return request

    def resubmit(self, actor=None):
        request = self.request
        if request.status != Request.Status.RETURNED:
            raise RoutingError("Seule une demande retournée peut être resoumise.")

        request.full_clean()
        previous_status = request.status

        if request.request_type.resume_on_resubmit:
            request.status = Request.Status.PENDING
            request.save()
            self._activate_level(request.current_level)
        else:
            snapshot = self._build_snapshot()
            request.current_level = snapshot[0]["level"] if snapshot else 1
            request.snapshot_metadata = {"workflow_snapshot": snapshot}
            request.status = Request.Status.PENDING
            request.save()
            if snapshot:
                self._activate_level(request.current_level)

        self._log(actor, ApprovalLog.ActionType.SUBMIT, previous_status, request.status)
        return request

    # ------------------------------------------------------------------
    def _log(self, actor, action_type, previous_status, new_status, comment=None, context=None):
        ApprovalLog.objects.create(
            request=self.request,
            actor=actor,
            action_type=action_type,
            previous_status=previous_status,
            new_status=new_status,
            comment=comment,
            context=context or {},
        )
