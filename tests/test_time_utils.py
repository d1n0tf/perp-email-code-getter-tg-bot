import unittest
from datetime import date, datetime, timezone

from src.time_utils import moscow_end_of_day, to_moscow, to_utc
from src.tokens import format_datetime, input_datetime, parse_admin_datetime, parse_datetime


class MoscowTimeTests(unittest.TestCase):
    def test_utc_values_are_displayed_in_moscow_time(self):
        stored = datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc)

        self.assertEqual(to_moscow(stored).strftime("%Y-%m-%d %H:%M"), "2026-08-17 12:30")
        self.assertEqual(format_datetime(stored), "17.08.2026 12:30")
        self.assertEqual(input_datetime(stored), "2026-08-17T12:30")

    def test_admin_datetime_is_saved_as_utc(self):
        self.assertEqual(
            parse_admin_datetime("2026-08-17T12:30"),
            datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc),
        )
        self.assertEqual(
            to_utc(datetime(2026, 8, 17, 12, 30)),
            datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc),
        )

    def test_legacy_naive_storage_values_remain_utc(self):
        self.assertEqual(
            parse_datetime("2026-08-17T09:30:00"),
            datetime(2026, 8, 17, 9, 30, tzinfo=timezone.utc),
        )

    def test_moscow_end_of_day_is_stored_in_utc(self):
        self.assertEqual(
            moscow_end_of_day(date(2026, 8, 17)),
            datetime(2026, 8, 17, 20, 59, 59, 999999, tzinfo=timezone.utc),
        )


if __name__ == "__main__":
    unittest.main()
