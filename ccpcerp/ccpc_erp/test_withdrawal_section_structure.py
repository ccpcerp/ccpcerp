# Copyright (c) 2026, CCPC and contributors
# For license information, please see license.txt

from unittest import TestCase

from ccpcerp.setup import WITHDRAWAL_SECTION_STRUCTURE


class TestWithdrawalSectionStructure(TestCase):
	def test_rmc_oil_refinery_plants_are_departments(self):
		rmc = WITHDRAWAL_SECTION_STRUCTURE["Ricor Mills Corporation"]

		self.assertNotIn("Oil Refinery Plant", rmc)
		self.assertEqual(rmc["Oil Refinery Plant 1"], ())
		self.assertEqual(rmc["Oil Refinery Plant 2"], ())

	def test_rmc_operational_departments_are_seeded(self):
		rmc = WITHDRAWAL_SECTION_STRUCTURE["Ricor Mills Corporation"]

		self.assertIn("Glucose Plant", rmc)
		self.assertIn("Maintenance Department", rmc)
