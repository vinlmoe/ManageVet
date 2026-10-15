"""Tests d'intégration de la résolution d'un trimestre."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting import pcg
from accounting.models import JournalLine
from accounting.services import account_balance
from clinics.services import acquire_equipment, create_clinic
from decisions.models import PendingDecision
from engine.resolution import resolve_quarter
from game.catalog import seed_catalog
from game.map import generate_zones
from game.models import GameServer


def make_server(name="Test", seed=1):
    """Serveur 10x10 avec deux cliniques A (2,2) et B (7,7), stocks cibles à 100."""
    server = GameServer.objects.create(
        name=name, grid_width=10, grid_height=10, seed=seed, parameters={"recruitment_enabled": 0}
    )
    generate_zones(server, towns=1)
    User = get_user_model()
    a = create_clinic(server, User.objects.create(username=f"{name}-a"), "A", 2, 2)
    b = create_clinic(server, User.objects.create(username=f"{name}-b"), "B", 7, 7)
    for clinic in (a, b):
        clinic.stock.update(target_quantity=100)
    server.start()
    return server, a, b


class ResolutionTestCase(TestCase):
    def setUp(self):
        seed_catalog()
        self.server, self.a, self.b = make_server()

    def decide(self, clinic, kind, **payload):
        return PendingDecision.objects.create(clinic=clinic, quarter=self.server.current_quarter, kind=kind, payload=payload)

    def test_journal_is_balanced_and_cash_matches_bank(self):
        resolve_quarter(self.server)
        lines = JournalLine.objects.all()
        self.assertEqual(sum(l.debit for l in lines), sum(l.credit for l in lines))
        for clinic in (self.a, self.b):
            clinic.refresh_from_db()
            self.assertEqual(clinic.cash, account_balance(clinic, pcg.BANK))

    def test_report_result_matches_cash_flow(self):
        cash_before = self.a.cash
        report = resolve_quarter(self.server)[0]
        self.a.refresh_from_db()
        # Sans investissement, Δtrésorerie = résultat + variation de stock reprise - amortissements (nuls ici)
        delta_stock = account_balance(self.a, pcg.STOCK_GOODS)
        self.assertEqual(self.a.cash - cash_before, report.net_result - delta_stock)
        self.assertGreater(report.revenue_services, 0)
        self.assertGreater(report.revenue_goods, 0)
        self.assertEqual(report.stock_value_end, delta_stock)

    def test_server_advances_then_finishes(self):
        for expected in (2, 3, 4):
            resolve_quarter(self.server)
            self.server.refresh_from_db()
            self.assertEqual(self.server.current_quarter, expected)
        resolve_quarter(self.server)
        self.server.refresh_from_db()
        self.assertEqual(self.server.status, GameServer.Status.FINISHED)
        with self.assertRaises(ValueError):
            resolve_quarter(self.server)

    def test_lower_prices_attract_more_patients(self):
        baseline = {r.clinic.name: r for r in resolve_quarter(self.server)}
        server, a, _ = make_server("Prix bas")
        for cs in a.services.all():
            self.decide(a, PendingDecision.Kind.SET_SERVICE, service=cs.service_type.code, price=str(cs.price * Decimal("0.7")))
        cheap = {r.clinic.name: r for r in resolve_quarter(server)}
        self.assertGreater(cheap["A"].market_share, baseline["A"].market_share)
        self.assertLess(cheap["B"].market_share, baseline["B"].market_share)

    def test_decisions_applied_or_rejected(self):
        ok = self.decide(self.a, PendingDecision.Kind.HIRE, role="vet", name="Dr X", gross_monthly_salary="4200")
        bad = self.decide(self.a, PendingDecision.Kind.SET_SERVICE, service="radiographie", active=True)
        resolve_quarter(self.server)
        ok.refresh_from_db()
        bad.refresh_from_db()
        self.assertEqual(ok.status, PendingDecision.Status.APPLIED)
        self.assertEqual(bad.status, PendingDecision.Status.REJECTED)
        self.assertEqual(self.a.staff.filter(role="vet").count(), 2)

    def test_equipment_purchase_is_capitalised_and_depreciated(self):
        acquire_equipment(self.a, "lab-biochimie", "purchase")
        resolve_quarter(self.server)
        self.assertEqual(account_balance(self.a, pcg.EQUIPMENT), Decimal("18000"))
        self.assertEqual(account_balance(self.a, pcg.EQUIPMENT_DEPRECIATION), Decimal("-900"))
        self.assertTrue(self.a.services.get(service_type__code="biochimie").active)

    def test_lease_is_an_expense(self):
        acquire_equipment(self.b, "echographe", "lease")
        resolve_quarter(self.server)
        self.assertEqual(account_balance(self.b, pcg.LEASING), Decimal("2100"))
        self.assertEqual(account_balance(self.b, pcg.EQUIPMENT), Decimal("0"))

    def test_restock_up_to_target(self):
        resolve_quarter(self.server)
        purchases = account_balance(self.a, pcg.PURCHASES_GOODS)
        expected = sum(item.product_line.unit_cost * 100 for item in self.a.stock.select_related("product_line"))
        self.assertEqual(purchases, expected)

    def test_determinism(self):
        first = [(r.clinic_id, r.net_result) for r in resolve_quarter(self.server)]
        self.assertTrue(all(isinstance(v, Decimal) for _, v in first))
        # Même graine, même configuration => mêmes résultats sur un serveur jumeau.
        twin, _, _ = make_server("Jumeau")
        second = resolve_quarter(twin)
        self.assertEqual([r.net_result for r in second], [v for _, v in first])

    def test_capacity_limits_acts(self):
        report = resolve_quarter(self.server)[0]
        workload = report.details["workload"]
        if workload["vet_hours_demanded"] > workload["vet_hours_available"]:
            self.assertLess(report.capacity_ratio, 1.0)
        else:
            self.assertEqual(report.capacity_ratio, 1.0)
