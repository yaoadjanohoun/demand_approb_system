import datetime

from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.test import TestCase

from .models import ApprovalLog, ApprovalRule, Delegation, Request, RequestType, UserProfile
from .services import RoutingError, WorkflowEngine


class WorkflowEngineTests(TestCase):
    def setUp(self):
        self.manager = User.objects.create_user("manager1", password="x")
        self.director = User.objects.create_user("director1", password="x")
        self.director_delegate = User.objects.create_user("director1_delegate", password="x")
        self.employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=self.employee, manager=self.manager, department_id=10, site_id=1)

        self.request_type = RequestType.objects.create(
            name="Achat Fournisseur IT",
            code="PURCHASE_IT",
            form_schema={"fields": [{"name": "montant", "type": "decimal", "required": True}]},
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"}
        )
        ApprovalRule.objects.create(
            request_type=self.request_type,
            level=2,
            criteria={"min_amount": 1000},
            approvers_config={"type": "user", "user_id": self.director.id},
        )

    def make_request(self, montant):
        return Request.objects.create(request_type=self.request_type, requester=self.employee, data={"montant": montant})

    def test_two_level_approval_advances_and_completes(self):
        request = self.make_request(2500)
        engine = WorkflowEngine(request)
        engine.submit(actor=self.employee)
        self.assertEqual(request.status, Request.Status.PENDING)
        self.assertEqual(request.current_level, 1)

        with self.assertRaises(RoutingError):
            engine.approve(self.director, "mauvais niveau")

        engine.approve(self.manager, "ok")
        request.refresh_from_db()
        self.assertEqual(request.current_level, 2)
        self.assertEqual(request.status, Request.Status.PENDING)

        engine.approve(self.director, "ok")
        request.refresh_from_db()
        self.assertEqual(request.status, Request.Status.APPROVED)
        self.assertIsNotNone(request.completed_at)

    def test_delegation_is_resolved_when_level_becomes_current(self):
        Delegation.objects.create(
            delegator=self.director,
            delegate=self.director_delegate,
            start_date=datetime.date.today() - datetime.timedelta(days=1),
            end_date=datetime.date.today() + datetime.timedelta(days=5),
        )
        request = self.make_request(2500)
        engine = WorkflowEngine(request)
        engine.submit(actor=self.employee)
        engine.approve(self.manager, "ok")
        request.refresh_from_db()

        level_2_entry = request.snapshot_metadata["workflow_snapshot"][1]
        self.assertEqual(level_2_entry["active_approver_ids"], [self.director_delegate.id])

        with self.assertRaises(RoutingError):
            engine.approve(self.director, "le titulaire n'est plus habilité pendant la délégation")

        engine.approve(self.director_delegate, "validé par le remplaçant")
        request.refresh_from_db()
        self.assertEqual(request.status, Request.Status.APPROVED)
        self.assertTrue(
            ApprovalLog.objects.filter(
                request=request, action_type=ApprovalLog.ActionType.DELEGATION_TRIGGERED
            ).exists()
        )

    def test_reject_requires_a_comment(self):
        request = self.make_request(100)
        engine = WorkflowEngine(request)
        engine.submit(actor=self.employee)

        with self.assertRaises(RoutingError):
            engine.reject(self.manager, "")

        engine.reject(self.manager, "Budget insuffisant")
        request.refresh_from_db()
        self.assertEqual(request.status, Request.Status.REJECTED)
        self.assertIsNotNone(request.completed_at)

    def test_return_then_resubmit_restarts_at_level_1_by_default(self):
        request = self.make_request(2000)
        engine = WorkflowEngine(request)
        engine.submit(actor=self.employee)
        engine.approve(self.manager, "ok")
        request.refresh_from_db()
        self.assertEqual(request.current_level, 2)

        engine.return_for_info(self.director, "précisez le fournisseur")
        request.refresh_from_db()
        self.assertEqual(request.status, Request.Status.RETURNED)

        engine.resubmit(actor=self.employee)
        request.refresh_from_db()
        self.assertEqual(request.status, Request.Status.PENDING)
        self.assertEqual(request.current_level, 1)

    def test_resubmit_resumes_at_current_level_when_configured(self):
        self.request_type.resume_on_resubmit = True
        self.request_type.save()

        request = self.make_request(2000)
        engine = WorkflowEngine(request)
        engine.submit(actor=self.employee)
        engine.approve(self.manager, "ok")
        request.refresh_from_db()
        self.assertEqual(request.current_level, 2)

        engine.return_for_info(self.director, "précisez le fournisseur")
        engine.resubmit(actor=self.employee)
        request.refresh_from_db()
        self.assertEqual(request.status, Request.Status.PENDING)
        self.assertEqual(request.current_level, 2)

    def test_no_matching_rule_auto_approves(self):
        request_type = RequestType.objects.create(
            name="Sans règle", code="NORULE", form_schema={"fields": []}
        )
        request = Request.objects.create(request_type=request_type, requester=self.employee, data={})
        engine = WorkflowEngine(request)
        engine.submit(actor=self.employee)
        request.refresh_from_db()
        self.assertEqual(request.status, Request.Status.APPROVED)

    def test_rule_change_after_submission_does_not_affect_snapshot(self):
        request = self.make_request(2500)
        engine = WorkflowEngine(request)
        engine.submit(actor=self.employee)
        request.refresh_from_db()
        snapshot_before = request.snapshot_metadata["workflow_snapshot"]

        new_approver = User.objects.create_user("director2", password="x")
        rule = ApprovalRule.objects.get(request_type=self.request_type, level=2)
        rule.approvers_config = {"type": "user", "user_id": new_approver.id}
        rule.save()

        request.refresh_from_db()
        self.assertEqual(request.snapshot_metadata["workflow_snapshot"], snapshot_before)


class InterventionTests(TestCase):
    """Bouton "Intervenir" pour les demandes bloquées (Manuel d'Administration §6.1)."""

    def setUp(self):
        self.admin = User.objects.create_user("admin1", password="x", is_staff=True)
        self.manager = User.objects.create_user("manager1", password="x")
        self.backup_manager = User.objects.create_user("backup_manager", password="x")
        self.employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(user=self.employee, manager=self.manager)

        self.request_type = RequestType.objects.create(
            name="Note de frais", code="EXPENSE",
            form_schema={"fields": [{"name": "montant", "type": "decimal", "required": True}]},
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, criteria={}, approvers_config={"type": "manager"}
        )
        self.request = Request.objects.create(
            request_type=self.request_type, requester=self.employee, data={"montant": 500}
        )
        self.engine = WorkflowEngine(self.request)
        self.engine.submit(actor=self.employee)

    def test_force_advance_requires_comment(self):
        with self.assertRaises(RoutingError):
            self.engine.force_advance(self.admin, "")

    def test_force_advance_completes_request_when_last_level(self):
        self.engine.force_advance(self.admin, "Manager parti sans délégation")
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, Request.Status.APPROVED)
        self.assertTrue(
            ApprovalLog.objects.filter(
                request=self.request, action_type=ApprovalLog.ActionType.FORCE_ADVANCE
            ).exists()
        )

    def test_reassign_changes_effective_approver(self):
        self.engine.reassign(self.admin, [self.backup_manager.id], "Manager en arrêt maladie")
        self.request.refresh_from_db()
        self.assertEqual(
            WorkflowEngine(self.request).get_effective_approvers(), [self.backup_manager.id]
        )
        # Le titulaire d'origine n'est plus habilité, le remplaçant désigné l'est.
        with self.assertRaises(RoutingError):
            WorkflowEngine(self.request).approve(self.manager, "ok")
        WorkflowEngine(self.request).approve(self.backup_manager, "ok")
        self.request.refresh_from_db()
        self.assertEqual(self.request.status, Request.Status.APPROVED)
        self.assertTrue(
            ApprovalLog.objects.filter(
                request=self.request, action_type=ApprovalLog.ActionType.REASSIGN
            ).exists()
        )

    def test_reassign_requires_new_approver(self):
        with self.assertRaises(RoutingError):
            self.engine.reassign(self.admin, [], "commentaire")

    def test_intervention_only_allowed_while_pending(self):
        self.engine.reject(self.manager, "refus")
        self.request.refresh_from_db()
        with self.assertRaises(RoutingError):
            WorkflowEngine(self.request).force_advance(self.admin, "trop tard")


class ConflictAndPriorityTests(TestCase):
    """Règles qui se chevauchent au même niveau (Manuel d'Administration §4.3)."""

    def setUp(self):
        self.manager_a = User.objects.create_user("approver_a", password="x")
        self.manager_b = User.objects.create_user("approver_b", password="x")
        self.employee = User.objects.create_user("employee1", password="x")
        UserProfile.objects.create(
            user=self.employee, department_id=10, site_id=1, country_code="FR"
        )
        self.request_type = RequestType.objects.create(
            name="Achat", code="PURCHASE",
            form_schema={"fields": [{"name": "montant", "type": "decimal", "required": True}]},
        )

    def make_request(self, montant=500):
        return Request.objects.create(
            request_type=self.request_type, requester=self.employee, data={"montant": montant}
        )

    def test_exact_duplicate_active_rule_rejected(self):
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1,
            criteria={"min_amount": 500}, approvers_config={"type": "user", "user_id": self.manager_a.id},
        )
        duplicate = ApprovalRule(
            request_type=self.request_type, level=1,
            criteria={"min_amount": 500}, approvers_config={"type": "user", "user_id": self.manager_b.id},
        )
        with self.assertRaises(ValidationError):
            duplicate.full_clean()

    def test_duplicate_allowed_if_other_rule_inactive(self):
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1, is_active=False,
            criteria={"min_amount": 500}, approvers_config={"type": "user", "user_id": self.manager_a.id},
        )
        same_criteria = ApprovalRule(
            request_type=self.request_type, level=1,
            criteria={"min_amount": 500}, approvers_config={"type": "user", "user_id": self.manager_b.id},
        )
        same_criteria.full_clean()  # ne doit pas lever d'exception

    def test_most_specific_rule_wins_over_default(self):
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1,
            criteria={}, approvers_config={"type": "user", "user_id": self.manager_a.id},
        )
        ApprovalRule.objects.create(
            request_type=self.request_type, level=1,
            criteria={"department_ids": [10]}, approvers_config={"type": "user", "user_id": self.manager_b.id},
        )
        request = self.make_request()
        WorkflowEngine(request).submit(actor=self.employee)
        request.refresh_from_db()
        entry = request.snapshot_metadata["workflow_snapshot"][0]
        self.assertEqual(entry["approver_ids"], [self.manager_b.id])
        self.assertNotIn("conflict", entry)

    def test_tie_is_broken_by_most_recently_updated_and_recorded(self):
        rule_site = ApprovalRule.objects.create(
            request_type=self.request_type, level=1,
            criteria={"site_id": 1}, approvers_config={"type": "user", "user_id": self.manager_a.id},
        )
        rule_country = ApprovalRule.objects.create(
            request_type=self.request_type, level=1,
            criteria={"country_code": "FR"}, approvers_config={"type": "user", "user_id": self.manager_b.id},
        )
        # rule_country a été créée en dernier, donc updated_at plus récent : elle doit gagner.
        request = self.make_request()
        WorkflowEngine(request).submit(actor=self.employee)
        request.refresh_from_db()
        entry = request.snapshot_metadata["workflow_snapshot"][0]
        self.assertEqual(entry["approver_ids"], [self.manager_b.id])
        self.assertEqual(entry["conflict"]["selected_rule_id"], rule_country.id)
        self.assertEqual(entry["conflict"]["tied_rule_ids"], [rule_site.id])

        # Si on retouche rule_site après coup, c'est elle qui doit désormais l'emporter.
        rule_site.save()
        request2 = self.make_request()
        WorkflowEngine(request2).submit(actor=self.employee)
        request2.refresh_from_db()
        entry2 = request2.snapshot_metadata["workflow_snapshot"][0]
        self.assertEqual(entry2["approver_ids"], [self.manager_a.id])

    def test_request_type_admin_flags_missing_default_rule_at_last_level(self):
        from .admin import RequestTypeAdmin

        admin_instance = RequestTypeAdmin(RequestType, admin_site=None)
        self.assertTrue(admin_instance.default_rule_display(self.request_type))  # pas de règle du tout

        ApprovalRule.objects.create(
            request_type=self.request_type, level=1,
            criteria={"min_amount": 1000}, approvers_config={"type": "user", "user_id": self.manager_a.id},
        )
        self.assertFalse(admin_instance.default_rule_display(self.request_type))

        ApprovalRule.objects.create(
            request_type=self.request_type, level=1,
            criteria={}, approvers_config={"type": "user", "user_id": self.manager_b.id},
        )
        self.assertTrue(admin_instance.default_rule_display(self.request_type))
