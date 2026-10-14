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
from engine.resolution import permanence_fte, resolve_quarter, vet_pools
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
        surgery = self.clinic.services.get(service_type__code="chirurgie-specialisee")
        consult = self.clinic.services.get(service_type__code="consultation-chirurgicale")
        hire(self.clinic, "vet", "Dr Os", Decimal("6500"), level=5, specialty="chirurgie")
        consult.refresh_from_db()
        surgery.refresh_from_db()
        self.assertTrue(consult.active)
        self.assertFalse(surgery.active)  # il faut aussi le bloc de chirurgie spécialisée
        acquire_equipment(self.clinic, "bloc-orthopedie", "lease")
        surgery.refresh_from_db()
        self.assertTrue(surgery.active)
        self.assertFalse(self.clinic.services.get(service_type__code="consultation-medecine-interne").active)

    def test_specialist_acts_draw_on_regional_referrals(self):
        """Le chirurgien spécialiste reçoit des cas de toute la région, pas seulement de sa patientèle."""
        hire(self.clinic, "vet", "Dr Os", Decimal("6500"), level=5, specialty="chirurgie")
        acquire_equipment(self.clinic, "bloc-orthopedie", "lease")
        reports = {r.clinic.name: r for r in resolve_quarter(self.server)}
        report = reports["A"]
        own_patients = report.patients_dogs + report.patients_cats
        acts = report.details["services"]["chirurgie-specialisee"]["acts"]
        self.assertGreater(acts, own_patients * 0.0055 * 1.2)  # bien plus que sa seule patientèle
        self.assertNotIn("chirurgie-specialisee", reports["B"].details["services"])  # B n'a pas de chirurgien

    def test_low_cost_cannot_offer_specialist_acts(self):
        hire(self.clinic, "vet", "Dr Os", Decimal("6500"), level=5, specialty="chirurgie")
        acquire_equipment(self.clinic, "bloc-orthopedie", "lease")
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
    """CHV : fondateur + 2 généralistes (échelon 3), 6 spécialistes (échelon 5), 6 ASV titrées."""

    def setUp(self):
        seed_catalog()
        self.server = GameServer.objects.create(name="CHV", grid_width=10, grid_height=10, seed=6)
        generate_zones(self.server, towns=1)
        self.clinic = create_clinic(self.server, get_user_model().objects.create(username="c"), "C", 5, 5)
        for _ in range(2):
            hire(self.clinic, "vet", "Véto", Decimal("4000"))
        for specialty in ("chirurgie", "chirurgie", "medecine_interne", "medecine_interne", "imagerie", "imagerie"):
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
        self.clinic.refresh_from_db()

    def test_is_chv(self):
        self.assertEqual(self.clinic.format, "chv")

    def test_permanence_taken_on_generalists_first(self):
        self.assertAlmostEqual(permanence_fte(P), 3.1, places=1)
        lost = permanence_fte(P) * (1 - P.chv_night_productivity) * P.vet_hours_per_quarter
        pools = self.report.details["workload"]["pools"]
        self.assertAlmostEqual(pools["general"]["available"], 3 * 220 - lost, places=0)
        # Les spécialistes (échelon 5, productivité 1,15) gardent tout leur temps.
        self.assertAlmostEqual(pools["chirurgie"]["available"], 2 * 220 * 1.15, places=0)

    def test_permanence_overflows_on_specialists_without_generalists(self):
        for member in self.clinic.staff.filter(role="vet", specialty=""):
            member.left_quarter = 1
            member.save()
        pools = vet_pools(self.clinic, 2, P)
        self.assertEqual(pools["general"], 0.0)
        specialists = sum(h for k, h in pools.items() if k != "general")
        lost = permanence_fte(P) * (1 - P.chv_night_productivity) * P.vet_hours_per_quarter
        self.assertAlmostEqual(specialists, 6 * 220 * 1.15 - lost, places=3)

    def test_night_sunday_majorations_follow_ccn(self):
        line = JournalLine.objects.get(
            entry__clinic=self.clinic, entry__label__startswith="Majorations conventionnelles", account=pcg.SALARIES
        )
        vet_avg = (Decimal("4000") * 3 + Decimal("6000") * 6) / 9  # fondateur + 2 vétos à 4 000 €, 6 spécialistes
        expected = Decimal("1060") * (
            vet_avg / ccn.MONTHLY_HOURS * Decimal("0.20") + Decimal("2200") / ccn.MONTHLY_HOURS * Decimal("0.15")
        )
        self.assertAlmostEqual(line.debit, expected.quantize(Decimal("0.01")), delta=Decimal("0.02"))


class LevelEffectsTests(TestCase):
    """Points 1 à 3 : productivité, qualité perçue et qualification selon l'échelon."""

    def setUp(self):
        seed_catalog()

    def make(self, name, vets=(), asvs=(), specialists=(), parameters=None):
        server = GameServer.objects.create(
            name=name, grid_width=8, grid_height=8, seed=11, parameters=parameters or {}
        )
        generate_zones(server, towns=1)
        clinic = create_clinic(server, get_user_model().objects.create(username=name), "X", 4, 4)
        for level, salary in vets:
            hire(clinic, "vet", "Véto", Decimal(salary), level=level)
        for level, salary in asvs:
            hire(clinic, "asv", "Aux", Decimal(salary), level=level)
        for specialty in specialists:
            hire(clinic, "vet", "Spé", Decimal("6000"), level=5, specialty=specialty)
        server.start()
        return server, clinic

    def test_productivity_by_level(self):
        server, clinic = self.make("prod", vets=[(2, "2800"), (4, "3800")], asvs=[(3, "2000")])
        pools = vet_pools(clinic, 1, P)
        self.assertAlmostEqual(pools["general"], 220 * (1.0 + 0.8 + 1.1))  # fondateur échelon 3
        from engine.resolution import asv_hours

        self.assertAlmostEqual(asv_hours(clinic, 1, P), 420 * (1.0 + 0.9))  # ASV titrée + AVQ

    def test_specialist_does_not_help_general_activity(self):
        """Un spécialiste ne fait que les actes de sa discipline : il ne soulage pas les consultations."""
        asvs = [(5, "2200")] * 4
        _, _ = self.make("seul", asvs=asvs)
        _, _ = self.make("avec-chir", asvs=asvs, specialists=["chirurgie"])
        reports = {
            name: resolve_quarter(GameServer.objects.get(name=name))[0] for name in ("seul", "avec-chir")
        }
        alone, with_surgeon = reports["seul"], reports["avec-chir"]
        self.assertLess(alone.details["workload"]["pools"]["general"]["available"],
                        alone.details["workload"]["pools"]["general"]["demanded"])  # généraliste saturé
        self.assertEqual(
            alone.details["services"]["consultation"]["acts"], with_surgeon.details["services"]["consultation"]["acts"]
        )
        self.assertIn("consultation-chirurgicale", with_surgeon.details["services"])

    def test_experienced_team_builds_reputation(self):
        many_asv = [(5, "2200")] * 3
        light = {"demand_multiplier": 0.2}  # capacité large : seule l'expérience diffère
        self.make("junior", vets=[(2, "2800")] * 4, asvs=many_asv, parameters=light)
        self.make("senior", vets=[(4, "3800")] * 4, asvs=many_asv, parameters=light)
        rep = {}
        for name in ("junior", "senior"):
            report = resolve_quarter(GameServer.objects.get(name=name))[0]
            self.assertEqual(round(report.capacity_ratio, 6), 1.0)  # même qualité de service
            rep[name] = report.reputation
        self.assertGreater(rep["senior"], rep["junior"])

    def test_experience_scores(self):
        self.assertEqual(ccn.experience_score("vet", 2), -1.0)
        self.assertEqual(ccn.experience_score("vet", 5), 1.0)
        self.assertEqual(ccn.experience_score("asv", 4), 0.0)

    def test_only_titled_asv_qualify_for_chv(self):
        from clinics.formats import missing_chv_requirements

        _, clinic = self.make("avq", asvs=[(3, "2000")] * 6)
        missing = missing_chv_requirements(clinic, 1, P)
        self.assertTrue(any("ASV titrées (échelon 5) à temps plein (actuellement 1)" in m for m in missing))
