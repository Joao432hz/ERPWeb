# ERPWeb/finance/tests.py
from __future__ import annotations

import json
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.core.exceptions import ValidationError
from django.db import transaction
from django.test import TestCase
from django.utils import timezone

from finance.models import FinancialMovement

# Seguridad / RBAC propio del proyecto (custom)
from security.models import Role, Permission, RolePermission, UserRole

User = get_user_model()


# ------------------------------------------------------------
# Helpers RBAC (mínimo indispensable para pasar require_permission)
# ------------------------------------------------------------

def _ensure_perm(code: str) -> Permission:
    p, _ = Permission.objects.get_or_create(code=code, defaults={"description": code})
    return p


def _ensure_role(name: str) -> Role:
    r, _ = Role.objects.get_or_create(name=name, defaults={"description": name, "is_active": True})
    if not r.is_active:
        r.is_active = True
        r.save(update_fields=["is_active"])
    return r


def _grant(role: Role, perm_code: str):
    p = _ensure_perm(perm_code)
    RolePermission.objects.get_or_create(role=role, permission=p)


def _mk_user(username: str, password: str = "test123"):
    u, _ = User.objects.get_or_create(username=username)
    u.set_password(password)
    if hasattr(u, "is_active") and not u.is_active:
        u.is_active = True
    u.save()
    return u


def _login_with_perms(testcase: TestCase, username: str, perm_codes: list[str]):
    """
    Crea usuario, rol, asigna permisos y hace force_login (evita CSRF en tests).
    """
    u = _mk_user(username)
    role = _ensure_role(f"role_{username}")
    for code in perm_codes:
        _grant(role, code)

    UserRole.objects.get_or_create(user=u, role=role)

    testcase.client.force_login(u)
    return u


# ------------------------------------------------------------
# Tests de MODELO (reglas fuertes vendibles)
# ------------------------------------------------------------

class FinancialMovementModelTests(TestCase):
    def test_pay_requires_open_and_amount_gt_zero(self):
        fm = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.RECEIVABLE,
            source_type=FinancialMovement.SourceType.SALE,
            source_id=999,
            amount=Decimal("0.00"),
            status=FinancialMovement.Status.OPEN,
        )

        with self.assertRaises(ValidationError):
            fm.pay()

        fm.amount = Decimal("10.00")
        fm.save()

        fm.pay()
        fm.refresh_from_db()
        self.assertEqual(fm.status, FinancialMovement.Status.PAID)
        self.assertIsNotNone(fm.paid_at)

        # pagar dos veces
        with self.assertRaises(ValidationError):
            fm.pay()

    def test_void_is_terminal_and_clears_paid_at(self):
        fm = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.PAYABLE,
            source_type=FinancialMovement.SourceType.PURCHASE,
            source_id=123,
            amount=Decimal("100.00"),
            status=FinancialMovement.Status.OPEN,
        )

        fm.void("motivo")
        fm.refresh_from_db()
        self.assertEqual(fm.status, FinancialMovement.Status.VOID)
        self.assertIsNone(fm.paid_at)
        self.assertTrue("motivo" in (fm.notes or ""))

        # idempotente: no explota ni cambia a algo raro
        fm.void("otro motivo")
        fm.refresh_from_db()
        self.assertEqual(fm.status, FinancialMovement.Status.VOID)

    def test_void_cannot_void_paid(self):
        fm = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.RECEIVABLE,
            source_type=FinancialMovement.SourceType.SALE,
            source_id=321,
            amount=Decimal("50.00"),
            status=FinancialMovement.Status.OPEN,
        )
        fm.pay()
        fm.refresh_from_db()
        self.assertEqual(fm.status, FinancialMovement.Status.PAID)

        with self.assertRaises(ValidationError):
            fm.void("no deberia")

    def test_paid_at_behavior(self):
        fm = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.RECEIVABLE,
            source_type=FinancialMovement.SourceType.SALE,
            source_id=777,
            amount=Decimal("20.00"),
            status=FinancialMovement.Status.OPEN,
        )
        self.assertIsNone(fm.paid_at)

        fm.pay()
        fm.refresh_from_db()
        self.assertIsNotNone(fm.paid_at)

        # En tu modelo actual: si status != PAID, save() fuerza paid_at = None.
        # O sea: NO debe lanzar ValidationError, debe limpiar paid_at.
        fm2 = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.PAYABLE,
            source_type=FinancialMovement.SourceType.PURCHASE,
            source_id=778,
            amount=Decimal("20.00"),
            status=FinancialMovement.Status.OPEN,
        )
        fm2.status = FinancialMovement.Status.VOID
        fm2.paid_at = timezone.now()
        fm2.save()
        fm2.refresh_from_db()
        self.assertEqual(fm2.status, FinancialMovement.Status.VOID)
        self.assertIsNone(fm2.paid_at)

    def test_immutability_after_closed(self):
        fm = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.RECEIVABLE,
            source_type=FinancialMovement.SourceType.SALE,
            source_id=888,
            amount=Decimal("100.00"),
            status=FinancialMovement.Status.OPEN,
        )
        fm.pay()
        fm.refresh_from_db()

        # Intentar cambiar amount luego de PAID
        fm.amount = Decimal("999.00")
        with self.assertRaises(ValidationError):
            fm.save()

        # Intentar cambiar source_id luego de PAID
        fm = FinancialMovement.objects.get(pk=fm.pk)
        fm.source_id = 999
        with self.assertRaises(ValidationError):
            fm.save()

    def test_unique_constraint_per_source(self):
        FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.RECEIVABLE,
            source_type=FinancialMovement.SourceType.SALE,
            source_id=111,
            amount=Decimal("10.00"),
            status=FinancialMovement.Status.OPEN,
        )

        # Tu save() corre full_clean(), entonces el UniqueConstraint se detecta
        # como ValidationError (no IntegrityError).
        with self.assertRaises(ValidationError):
            with transaction.atomic():
                FinancialMovement.objects.create(
                    movement_type=FinancialMovement.MovementType.RECEIVABLE,
                    source_type=FinancialMovement.SourceType.SALE,
                    source_id=111,
                    amount=Decimal("10.00"),
                    status=FinancialMovement.Status.OPEN,
                )


# ------------------------------------------------------------
# Tests de API (views): movements, summary, pay
# ------------------------------------------------------------

class FinancialViewsTests(TestCase):
    def setUp(self):
        # usuario con view+pay
        _login_with_perms(
            self,
            username="tester_finance",
            perm_codes=["finance.movement.view", "finance.movement.pay"],
        )

        # Cargamos data variada
        self.fm_open_receivable = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.RECEIVABLE,
            source_type=FinancialMovement.SourceType.SALE,
            source_id=1,
            amount=Decimal("1500.00"),
            status=FinancialMovement.Status.OPEN,
            notes="Auto: Sale CONFIRMED (SO #1)",
        )
        self.fm_open_payable = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.PAYABLE,
            source_type=FinancialMovement.SourceType.PURCHASE,
            source_id=2,
            amount=Decimal("1000.00"),
            status=FinancialMovement.Status.OPEN,
            notes="Auto: Purchase RECEIVED (PO #2)",
        )
        self.fm_void = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.RECEIVABLE,
            source_type=FinancialMovement.SourceType.SALE,
            source_id=3,
            amount=Decimal("0.00"),
            status=FinancialMovement.Status.OPEN,
            notes="tmp",
        )
        self.fm_void.void("test void")
        self.fm_void.refresh_from_db()

        self.fm_paid = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.RECEIVABLE,
            source_type=FinancialMovement.SourceType.SALE,
            source_id=4,
            amount=Decimal("500.00"),
            status=FinancialMovement.Status.OPEN,
        )
        self.fm_paid.pay()
        self.fm_paid.refresh_from_db()

    def _get_json(self, url: str):
        resp = self.client.get(url)
        self.assertEqual(resp.status_code, 200, resp.content)
        return json.loads(resp.content.decode("utf-8"))

    def _post_json(self, url: str, data: dict | None = None):
        body = json.dumps(data or {})
        resp = self.client.post(url, data=body, content_type="application/json")
        return resp

    def test_movements_list_filters_and_ordering(self):
        data = self._get_json("/api/finance/movements/?status=OPEN&ordering=-id")
        self.assertEqual(data["status"], "ok")
        self.assertTrue(data["count"] >= 2)
        for item in data["results"]:
            self.assertEqual(item["status"], "OPEN")

        # invalid ordering => 400
        resp = self.client.get("/api/finance/movements/?ordering=DROP_TABLE")
        self.assertEqual(resp.status_code, 400)

    def test_summary_contains_void_bucket(self):
        data = self._get_json("/api/finance/summary/")
        self.assertEqual(data["status"], "ok")
        self.assertIn("payables", data)
        self.assertIn("receivables", data)
        # Debe incluir void (tu build_financial_summary actual lo devuelve siempre)
        self.assertIn("void", data["payables"])
        self.assertIn("void", data["receivables"])

    def test_pay_endpoint_success(self):
        resp = self._post_json(f"/api/finance/movements/{self.fm_open_receivable.id}/pay/")
        self.assertEqual(resp.status_code, 200, resp.content)

        payload = json.loads(resp.content.decode("utf-8"))
        self.assertEqual(payload["status"], "ok")
        self.assertEqual(payload["movement_id"], self.fm_open_receivable.id)
        self.assertEqual(payload["new_status"], "PAID")
        self.assertIsNotNone(payload["paid_at"])

        self.fm_open_receivable.refresh_from_db()
        self.assertEqual(self.fm_open_receivable.status, FinancialMovement.Status.PAID)

    def test_pay_endpoint_rejects_void(self):
        resp = self._post_json(f"/api/finance/movements/{self.fm_void.id}/pay/")
        self.assertEqual(resp.status_code, 400)
        body = json.loads(resp.content.decode("utf-8"))
        self.assertIn("VOID", body.get("detail", ""))

    def test_pay_endpoint_rejects_already_paid(self):
        resp = self._post_json(f"/api/finance/movements/{self.fm_paid.id}/pay/")
        self.assertEqual(resp.status_code, 400)
        body = json.loads(resp.content.decode("utf-8"))
        self.assertTrue("PAID" in body.get("detail", "") or "ya está" in body.get("detail", ""))

    def test_pay_endpoint_rejects_amount_zero(self):
        fm = FinancialMovement.objects.create(
            movement_type=FinancialMovement.MovementType.PAYABLE,
            source_type=FinancialMovement.SourceType.PURCHASE,
            source_id=9999,
            amount=Decimal("0.00"),
            status=FinancialMovement.Status.OPEN,
        )
        resp = self._post_json(f"/api/finance/movements/{fm.id}/pay/")
        self.assertEqual(resp.status_code, 400)
        body = json.loads(resp.content.decode("utf-8"))
        self.assertIn("amount", str(body))

    def test_permissions_required(self):
        # Nuevo usuario SIN permisos finance
        self.client.logout()
        _login_with_perms(self, "no_fin_perm", perm_codes=[])

        # movimientos => forbidden (tu middleware/decorator puede devolver 403 o redirigir)
        resp = self.client.get("/api/finance/movements/")
        self.assertIn(resp.status_code, (302, 403))

        # pay => forbidden
        resp2 = self.client.post(
            f"/api/finance/movements/{self.fm_open_payable.id}/pay/",
            data="{}",
            content_type="application/json",
        )
        self.assertIn(resp2.status_code, (302, 403))
