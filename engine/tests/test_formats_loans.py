"""Formats d'établissement (low-cost, CHV) et emprunts bancaires."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase

from accounting import pcg
from accounting.loans import annuity, borrow, quote, repay_early
from accounting.models import JournalLine, Loan
from accounting.services import account_balance
from clinics.models import Clinic, ClinicService, ServiceType
from clinics.services import OperationError, acquire_equipment, create_clinic, hire
from decisions.models import PendingDecision
from engine.parameters import SimulationParameters
from engine.resolution import resolve_quarter
from game.catalog import ensure_clinic_offers, seed_catalog
from game.map import generate_zones
from game.models import GameServer

Kind = PendingDecision.Kind
P = SimulationParameters()
CHV_EQUIPMENT = (
    "radio-numerique", "echographe", "scanner", "lab-biochimie", "lab-hemato", "lab-ionogramme",
    "bloc-orthopedie", "endoscope",
)
CHV_SPECIALISTS = ("chirurgie", "chirurgie", "medecine_interne", "medecine_interne", "imagerie", "imagerie")


class BaseCase(TestCase):
    def setUp(self):
        seed_catalog()
        self.server = GameServer.objects.create(
            name="Test", grid_width=10, grid_height=10, seed=2, total_quarters=8, parameters={"recruitment_enabled": 0}
        )
        generate_zones(self.server, towns=1)
        User = get_user_model()
        self.clinic = create_clinic(self.server, User.objects.create(username="a"), "A", 3, 3)
        self.other = create_clinic(self.server, User.objects.create(username="b"), "B", 7, 7)
        self.server.start()

    def decide(self, kind, clinic=None, **payload):
        clinic = clinic or self.clinic
        return PendingDecision.objects.create(
            clinic=clinic, quarter=self.server.current_quarter, kind=kind, payload=payload
        )

    def resolve(self):
        reports = {r.clinic_id: r for r in resolve_quarter(self.server)}
        self.server.refresh_from_db()
        self.clinic.refresh_from_db()
        return reports

    def make_chv_ready(self, clinic=None):
        """Équipe et plateau technique d'un CHV : 6 spécialistes (échelon 5) en plus du fondateur, 6 ASV titrées."""
        clinic = clinic or self.clinic
        for _ in range(2):
            hire(clinic, "vet", "Véto", Decimal("4000"))
        for specialty in CHV_SPECIALISTS:
            hire(clinic, "vet", "Spécialiste", Decimal("6500"), level=5, specialty=specialty)
        for _ in range(5):
            hire(clinic, "asv", "ASV", Decimal("2200"))
        for code in CHV_EQUIPMENT:
            acquire_equipment(clinic, code, "lease")

    def assert_books_consistent(self):
        lines = JournalLine.objects.all()
        self.assertEqual(sum(l.debit for l in lines), sum(l.credit for l in lines))
        for clinic in Clinic.objects.all():
            self.assertEqual(clinic.cash, account_balance(clinic, pcg.BANK))


class LoanTests(BaseCase):
    def test_annuity_formula(self):
        # 100 000 € à 4 % sur 2 ans (8 trimestres, taux trimestriel 1 %) -> 13 069,03 €
        self.assertEqual(annuity(Decimal("100000"), Decimal("0.04"), 8), Decimal("13069.03"))

    def test_full_life_of_a_loan(self):
        self.decide(Kind.BORROW, amount="40000", duration_quarters=4)
        self.resolve()
        loan = Loan.objects.get()
        self.assertEqual(account_balance(self.clinic, pcg.LOANS), Decimal("-40000"))
        self.assertEqual(loan.installments_paid, 0)  # première échéance au trimestre suivant
        for _ in range(4):
            self.resolve()
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.Status.REPAID)
        self.assertEqual(loan.installments_paid, 4)
        self.assertEqual(account_balance(self.clinic, pcg.LOANS), Decimal("0"))
        # Intérêts payés = total des échéances décaissées - capital emprunté.
        paid = sum(
            l.credit
            for l in JournalLine.objects.filter(
                entry__clinic=self.clinic, entry__label__startswith="Échéance", account=pcg.BANK
            )
        )
        interest = account_balance(self.clinic, pcg.LOAN_INTEREST)
        self.assertGreater(interest, 0)
        self.assertEqual(interest, paid - Decimal("40000"))
        self.assertLess(abs(paid - loan.quarterly_payment * 4), Decimal("0.05"))  # dernière échéance ajustée
        report = self.clinic.reports.get(quarter=2)
        self.assertGreater(report.financial_charges, 0)
        self.assert_books_consistent()

    def test_capacity_and_refusal(self):
        offer = quote(self.clinic, Decimal("1000000"), 20, P, 1)
        self.assertFalse(offer.accepted)
        self.assertIn("Capacité", offer.reason)
        self.assertFalse(quote(self.clinic, Decimal("50000"), 7, P, 1).accepted)  # durée non proposée
        bad = self.decide(Kind.BORROW, amount="900000", duration_quarters=20)
        self.resolve()
        bad.refresh_from_db()
        self.assertEqual(bad.status, PendingDecision.Status.REJECTED)
        self.assertIn("Prêt refusé", bad.message)

    def test_rate_grows_with_leverage(self):
        small = quote(self.clinic, Decimal("10000"), 20, P, 1)
        large = quote(self.clinic, Decimal("200000"), 20, P, 1)
        self.assertTrue(small.accepted and large.accepted)
        self.assertGreater(large.annual_rate, small.annual_rate)

    def test_early_repayment_with_penalty(self):
        loan = borrow(self.clinic, Decimal("50000"), 20, P)
        self.decide(Kind.REPAY_LOAN, loan_id=loan.pk)
        self.resolve()
        loan.refresh_from_db()
        self.assertEqual(loan.status, Loan.Status.REPAID)
        self.assertEqual(account_balance(self.clinic, pcg.LOANS), Decimal("0"))
        self.assertEqual(account_balance(self.clinic, pcg.OTHER_FINANCIAL), Decimal("1500.00"))
        with self.assertRaises(OperationError):
            repay_early(self.clinic, loan.pk, P)

    def test_report_tracks_debt_and_equity(self):
        borrow(self.clinic, Decimal("30000"), 12, P)
        report = self.resolve()[self.clinic.pk]
        self.assertEqual(report.debt_end, Decimal("30000"))
        self.assertEqual(report.equity_end, Decimal("80000") + report.net_result)


class LowCostTests(BaseCase):
    def test_switch_restricts_offer(self):
        acquire_equipment(self.clinic, "lab-biochimie", "lease")
        self.decide(Kind.SET_FORMAT, format="low_cost")
        self.resolve()
        self.assertEqual(self.clinic.format, Clinic.Format.LOW_COST)
        self.assertFalse(self.clinic.services.get(service_type__code="biochimie").active)
        self.assertTrue(self.clinic.services.get(service_type__code="vaccination").active)
        refused = self.decide(Kind.SET_SERVICE, service="biochimie", active=True)
        self.resolve()
        refused.refresh_from_db()
        self.assertEqual(refused.status, PendingDecision.Status.REJECTED)
        self.assertIn("low-cost", refused.message)

    def test_low_cost_acts_take_less_time_and_consumables(self):
        """Même clinique, même marché, capacité large (toute la demande est servie) : seul le format change."""

        def run(clinic_format):
            server = GameServer.objects.create(name=clinic_format, grid_width=6, grid_height=6, seed=4)
            generate_zones(server, towns=1)
            clinic = create_clinic(server, get_user_model().objects.create(username=clinic_format), "X", 3, 3)
            for _ in range(6):
                hire(clinic, "vet", "Véto", Decimal("4000"))
                hire(clinic, "asv", "ASV", Decimal("2200"))
            clinic.format = clinic_format
            clinic.save()
            server.start()
            report = resolve_quarter(server)[0]
            self.assertEqual(report.capacity_ratio, 1.0)
            return report.details["workload"]["vet_hours_demanded"] / report.acts, report.consumables / report.acts

        low_time, low_cons = run("low_cost")
        std_time, std_cons = run("standard")
        self.assertAlmostEqual(low_time / std_time, P.lowcost_time_factor, delta=0.02)
        self.assertAlmostEqual(float(low_cons / std_cons), P.lowcost_consumables_factor, delta=0.02)


class ChvTests(BaseCase):
    def test_switch_refused_without_requirements(self):
        decision = self.decide(Kind.SET_FORMAT, format="chv")
        self.resolve()
        decision.refresh_from_db()
        self.assertEqual(decision.status, PendingDecision.Status.REJECTED)
        self.assertIn("Conditions CHV non remplies", decision.message)
        self.assertEqual(self.clinic.format, Clinic.Format.STANDARD)

    def test_switch_counts_same_quarter_hires_and_equipment(self):
        # La décision de format est saisie EN PREMIER mais appliquée en dernier.
        self.decide(Kind.SET_FORMAT, format="chv")
        for _ in range(3):
            self.decide(Kind.HIRE, role="vet", name="Véto", gross_monthly_salary="4000", level=3)
        for specialty in CHV_SPECIALISTS:
            self.decide(Kind.HIRE, role="vet", name="Spé", gross_monthly_salary="6000", level=5, specialty=specialty)
        for _ in range(5):
            self.decide(Kind.HIRE, role="asv", name="ASV", gross_monthly_salary="2200", level=5)
        for code in CHV_EQUIPMENT:
            self.decide(Kind.ACQUIRE_EQUIPMENT, equipment=code, mode="lease")
        self.resolve()
        self.assertEqual(self.clinic.format, Clinic.Format.CHV)
        self.assertFalse(self.clinic.decisions.filter(status=PendingDecision.Status.REJECTED).exists())

    def test_chv_economics(self):
        self.make_chv_ready()
        self.decide(Kind.SET_FORMAT, format="chv")
        report = self.resolve()[self.clinic.pk]
        # Travaux immobilisés (2135) et amortis sur 40 trimestres (28135).
        self.assertEqual(account_balance(self.clinic, pcg.FITOUT), Decimal("60000"))
        self.assertEqual(account_balance(self.clinic, "28135"), Decimal("-1500"))
        # Prestations exclusives actives et facturées.
        self.assertTrue(self.clinic.services.get(service_type__code="urgences").active)
        self.assertIn("urgences", report.details["services"])
        # Surcoûts de fonctionnement 24 h/24 (en plus des 6 000 € de charges diverses).
        self.assertEqual(account_balance(self.clinic, pcg.OTHER_EXTERNAL), Decimal("14000"))
        self.assertEqual(report.clinic_format, "chv")
        self.assert_books_consistent()

    def test_chv_is_more_attractive(self):
        """À plateau technique et effectif identiques, le statut CHV attire plus de patients."""

        def market_share(as_chv):
            server = GameServer.objects.create(name=f"chv-{as_chv}", grid_width=10, grid_height=10, seed=2)
            generate_zones(server, towns=1)
            User = get_user_model()
            clinic = create_clinic(server, User.objects.create(username=f"x-{as_chv}"), "X", 3, 3)
            create_clinic(server, User.objects.create(username=f"y-{as_chv}"), "Y", 7, 7)
            self.make_chv_ready(clinic)
            server.start()
            if as_chv:
                PendingDecision.objects.create(clinic=clinic, quarter=1, kind=Kind.SET_FORMAT, payload={"format": "chv"})
            return {r.clinic.name: r for r in resolve_quarter(server)}["X"].market_share

        self.assertGreater(market_share(True), market_share(False))

    def test_loses_status_when_requirements_no_longer_met(self):
        self.make_chv_ready()
        self.decide(Kind.SET_FORMAT, format="chv")
        self.resolve()
        vet = self.clinic.staff.filter(role="vet").exclude(specialty="").first()  # un des 6 spécialistes
        self.decide(Kind.DISMISS, staff_id=vet.pk)
        self.resolve()  # le vétérinaire travaille encore ce trimestre
        self.assertEqual(self.clinic.format, Clinic.Format.CHV)
        report = self.resolve()[self.clinic.pk]
        self.assertEqual(self.clinic.format, Clinic.Format.STANDARD)
        self.assertTrue(any("Perte du statut CHV" in e for e in report.details["events"]))
        self.assertFalse(self.clinic.services.get(service_type__code="urgences").active)

    def test_fitout_paid_only_once(self):
        self.make_chv_ready()
        self.decide(Kind.SET_FORMAT, format="chv")
        self.resolve()
        self.decide(Kind.SET_FORMAT, format="standard")
        self.resolve()
        self.decide(Kind.SET_FORMAT, format="chv")
        self.resolve()
        self.assertEqual(self.clinic.format, Clinic.Format.CHV)
        self.assertEqual(account_balance(self.clinic, pcg.FITOUT), Decimal("60000"))

    def test_fitout_not_in_catalog(self):
        with self.assertRaises(OperationError):
            acquire_equipment(self.clinic, "amenagement-chv", "purchase")


class CatalogUpgradeTests(BaseCase):
    def test_existing_clinics_receive_new_services(self):
        ClinicService.objects.filter(service_type__code="urgences").delete()
        ensure_clinic_offers()
        cs = self.clinic.services.get(service_type__code="urgences")
        self.assertFalse(cs.active)
        self.assertEqual(cs.price, ServiceType.objects.get(code="urgences").reference_price)
