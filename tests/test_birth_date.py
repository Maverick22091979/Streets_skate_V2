import unittest
from datetime import date

from app.main import _parse_birth_date


class BirthDateTests(unittest.TestCase):
    def test_european_and_existing_iso_dates(self):
        self.assertEqual(_parse_birth_date("31/12/2000"), date(2000, 12, 31))
        self.assertEqual(_parse_birth_date("2000-12-31"), date(2000, 12, 31))

    def test_invalid_date_is_rejected(self):
        with self.assertRaises(ValueError):
            _parse_birth_date("31/02/2000")


if __name__ == "__main__": unittest.main()
