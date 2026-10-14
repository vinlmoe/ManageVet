"""Tests du modèle de demande (pur Python, sans base de données)."""

from unittest import TestCase

from engine import demand
from engine.parameters import SimulationParameters

P = SimulationParameters()


def offer(cid, x=0, y=0, price=1.0, offer_ratio=1.0, reputation=0.5):
    return demand.ClinicOffer(cid, x, y, price, offer_ratio, reputation)


class ZoneSharesTests(TestCase):
    def setUp(self):
        self.zone = demand.Zone(1, 5, 5, dogs=100, cats=100)

    def test_shares_sum_below_one(self):
        shares = demand.zone_shares(self.zone, [offer(1, 5, 5), offer(2, 8, 8)], P)
        self.assertLess(sum(shares.values()), 1.0)
        self.assertGreater(sum(shares.values()), 0.0)

    def test_closer_clinic_wins(self):
        shares = demand.zone_shares(self.zone, [offer(1, 5, 6), offer(2, 15, 15)], P)
        self.assertGreater(shares[1], shares[2])

    def test_cheaper_clinic_wins_at_equal_distance(self):
        shares = demand.zone_shares(self.zone, [offer(1, 5, 6, price=0.9), offer(2, 5, 4, price=1.2)], P)
        self.assertGreater(shares[1], shares[2])

    def test_broader_offer_and_reputation_attract(self):
        shares = demand.zone_shares(
            self.zone, [offer(1, 5, 6, offer_ratio=1.0, reputation=0.9), offer(2, 5, 4, offer_ratio=0.7, reputation=0.3)], P
        )
        self.assertGreater(shares[1], shares[2])

    def test_no_clinic_no_share(self):
        self.assertEqual(demand.zone_shares(self.zone, [], P), {})


class AllocationTests(TestCase):
    def test_allocation_conserves_animals(self):
        zones = [demand.Zone(i, i, 0, dogs=50, cats=70) for i in range(5)]
        alloc = demand.allocate_patients(zones, [offer(1, 0, 0), offer(2, 4, 0)], P)
        allocated = sum(a["dogs"] + a["cats"] for a in alloc.values())
        self.assertLessEqual(allocated, 5 * 120)
        self.assertGreater(allocated, 0)

    def test_demand_multiplier_scales_volume(self):
        zones = [demand.Zone(1, 0, 0, dogs=100, cats=100)]
        base = demand.allocate_patients(zones, [offer(1)], P)[1]["dogs"]
        shocked = demand.allocate_patients(zones, [offer(1)], SimulationParameters(demand_multiplier=0.5))[1]["dogs"]
        self.assertAlmostEqual(shocked, base / 2)


class ParametersTests(TestCase):
    def test_overrides_ignore_unknown_keys(self):
        p = SimulationParameters.from_overrides({"beta_price": "4", "unknown": 1})
        self.assertEqual(p.beta_price, 4.0)

    def test_price_volume_factor(self):
        self.assertAlmostEqual(demand.price_volume_factor(100, 100, 1.5), 1.0)
        self.assertLess(demand.price_volume_factor(120, 100, 1.5), 1.0)
