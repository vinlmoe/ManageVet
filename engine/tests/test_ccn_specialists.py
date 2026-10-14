"""Convention collective, vétérinaires spécialistes, bassin régional et permanence du CHV."""

from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from accounting import pcg
from accounting.models import JournalLine
from clinics import ccn
from clinics.services import OperationError, acquire_equipment, create_clinic, hire
from decisions.forms import HireForm
from decisions.models import PendingDecision
from engine import demand
from engine.parameters import SimulationParameters
from engine.resolution import permanence_fte, resolve_quarter
from game.catalog import seed_catalog
from game.map import generate_zones
from game.models import GameServer

P = SimulationParameters()


class GridTests(SimpleTestCase):
    def test_minimum_salaries_2026(self):
        # Coefficient x 17,96 € (avenant n° 91).
        self.assertEqual(ccn.minimum_salary("vet", 2, 17.96), Decimal("2729.92"))
        self.assertEqual(ccn.minimum_salary("vet", 5, 17.96), Decimal("4310.40"))
        self.assertEqual(ccn.minimum_salary("asv", 3, 17.96), Decimal("1975.60"))
        self.assertEqual(ccn.minimum_salary("asv", 5, 17.96), Decimal("2155.20"))

    def test_point_value_is_a_parameter(self):
        self.assertEqual(ccn.minimum_salary("asv", 5, 20), Decimal("2400.00"))

    def test_unknown_level(self):
        with self.assertRaises(ValueError):
            ccn.coefficient("asv", 1)

    def test_hire_form_rules(self):
        def errors(**data):
            form = HireForm({"name": "X", **data}, point_value=17.96)
            form.is_valid()
            return form.errors

        self.assertIn("level", errors(role="asv", level="2", gross_monthly_salary="3000"))  # échelon non proposé
        self.assertIn("gross_monthly_salary", errors(role="vet", level="5", gross_monthly_salary="3000"))  # < 4 310,40 €
        self.assertIn("specialty", errors(role="asv", level="5", specialty="chirurgie", gross_monthly_salary="5000"))
        self.assertIn("level", errors(role="vet", level="3", specialty="chirurgie", gross_monthly_salary="5000"))
        salary_error = errors(role="vet", level="4", gross_monthly_salary="3000")["gross_monthly_salary"][0]
        self.assertIn("3771.60", salary_error)
        self.assertEqual(errors(role="vet", level="5", specialty="imagerie", gross_monthly_salary="6000"), {})


class SpecialistTests(TestCase):
    def setUp(self):
        seed_catalog()
        self.server = GameServer.objects.create(name="Spé", grid_width=10, grid_height=10, seed=6)
        generate_zones(self.server, towns=1)
        User = get_user_model()
        self.clinic = create_clinic(self.server, User.objects.create(username="a"), "A", 3, 3)
        self.other = create_clinic(self.server, User.objects.create(username="b"), "B", 8, 8)
        self.server.start()

    def test_hire_respects_grid(self):
        with self.assertRaises(OperationError):
            hire(self.clinic, "asv", "ASV", Decimal("2000"))  # échelon 5 par défaut : minimum 2 155,20 €
        with self.assertRaises(OperationError):
            hire(self.clinic, "asv", "ASV", Decimal("3000"), specialty="chirurgie")
        with self.assertRaises(OperationError):
            hire(self.clinic, "vet", "Véto", Decimal("6000"), level=4, specialty="chirurgie")
        member = hire(self.clinic, "asv", "AVQ", Decimal("1980"), level=3)
        self.assertEqual(member.level_label, "Échelon 3 – auxiliaire vétérinaire qualifié (AVQ)")

    def test_specialist_enables_specialty_acts(self):
        cs = self.clinic.services.get(service_type__code="chirurgie-specialisee")
        self.assertFalse(cs.active)
        hire(self.clinic, "vet", "Dr Os", Decimal("6500"), level=5, specialty="chirurgie")
        cs.refresh_from_db()
        self.assertTrue(cs.active)
        self.assertFalse(self.clinic.services.get(service_type__code="consultation-medecine-interne").active)

    def test_specialist_acts_draw_on_regional_referrals(self):
        """Le chirurgien spécialiste reçoit des cas de toute la région, pas seulement de sa patientèle."""
        hire(self.clinic, "vet", "Dr Os", Decimal("6500"), level=5, specialty="chirurgie")
        reports = {r.clinic.name: r for r in resolve_quarter(self.server)}
        report = reports["A"]
        own_patients = report.patients_dogs + report.patients_cats
        acts = report.details["services"]["chirurgie-specialisee"]["acts"]
        self.assertGreater(acts, own_patients * 0.003 * 1.2)  # bien plus que sa seule patientèle
        self.assertNotIn("chirurgie-specialisee", reports["B"].details["services"])  # B n'a pas de chirurgien

    def test_low_cost_cannot_offer_specialist_acts(self):
        hire(self.clinic, "vet", "Dr Os", Decimal("6500"), level=5, specialty="chirurgie")
        PendingDecision.objects.create(
            clinic=self.clinic, quarter=1, kind=PendingDecision.Kind.SET_FORMAT, payload={"format": "low_cost"}
        )
        report = {r.clinic.name: r for r in resolve_quarter(self.server)}["A"]
        self.assertNotIn("chirurgie-specialisee", report.details["services"])


class ReferralDemandTests(SimpleTestCase):
    def test_referrals_travel_further(self):
        zones = [demand.Zone(1, 0, 0, dogs=100, cats=100), demand.Zone(2, 15, 0, dogs=100, cats=100)]
        clinic = demand.ClinicOffer(1, 0, 0, 1.0, 1.0, 0.5)
        near_far_regular = demand.allocate_patients(zones[1:], [clinic], P)[1]
        near_far_referral = demand.allocate_referrals(zones[1:], [clinic], P)
        self.assertGreater(near_far_referral[1], near_far_regular["dogs"] + near_far_regular["cats"])


class PermanenceTests(TestCase):
    def setUp(self):
        seed_catalog()
        self.server = GameServer.objects.create(name="CHV", grid_width=10, grid_height=10, seed=6)
        generate_zones(self.server, towns=1)
        self.clinic = create_clinic(self.server, get_user_model().objects.create(username="c"), "C", 5, 5)
        for _ in range(4):
            hire(self.clinic, "vet", "Véto", Decimal("4000"))
        for specialty in ("chirurgie", "medecine_interne"):
            hire(self.clinic, "vet", "Spé", Decimal("6000"), level=5, specialty=specialty)
        for _ in range(5):
            hire(self.clinic, "asv", "ASV", Decimal("2200"))
        for code in ("radio-numerique", "echographe", "scanner", "lab-biochimie", "lab-hemato", "lab-ionogramme"):
            acquire_equipment(self.clinic, code, "lease")
        self.server.start()
        PendingDecision.objects.create(
            clinic=self.clinic, quarter=1, kind=PendingDecision.Kind.SET_FORMAT, payload={"format": "chv"}
        )
        self.report = resolve_quarter(self.server)[0]

    def test_permanence_reduces_daytime_capacity(self):
        lost = permanence_fte(P) * (1 - P.chv_night_productivity)
        self.assertAlmostEqual(permanence_fte(P), 3.1, places=1)
        expected = round((7 - lost) * P.vet_hours_per_quarter, 1)
        self.assertAlmostEqual(self.report.details["workload"]["vet_hours_available"], expected, places=0)

    def test_night_sunday_majorations_follow_ccn(self):
        line = JournalLine.objects.get(
            entry__clinic=self.clinic, entry__label__startswith="Majorations conventionnelles", account=pcg.SALARIES
        )
        vet_avg = (Decimal("4000") * 5 + Decimal("6000") * 2) / 7  # fondateur + 4 vétos à 4 000 €, 2 spécialistes
        expected = Decimal("1060") * (
            vet_avg / ccn.MONTHLY_HOURS * Decimal("0.20") + Decimal("2200") / ccn.MONTHLY_HOURS * Decimal("0.15")
        )
        self.assertAlmostEqual(line.debit, expected.quantize(Decimal("0.01")), delta=Decimal("0.02"))
        self.assertEqual(line.account, pcg.SALARIES)
