import unittest

from random_rating_filter import filter_items_by_official_rating_threshold


class TestRandomRatingFilter(unittest.TestCase):
    def test_hide_threshold_and_above(self):
        order = ["G", "PG", "PG-13", "R", "NC-17"]
        items = [
            {"Id": "1", "OfficialRating": "PG"},
            {"Id": "2", "OfficialRating": "PG-13"},
            {"Id": "3", "OfficialRating": "R"},
            {"Id": "4", "OfficialRating": "NC-17"},
        ]

        filtered = filter_items_by_official_rating_threshold(items, "R", order)

        self.assertEqual([x["Id"] for x in filtered], ["1", "2"])

    def test_keep_unknown_or_unrated(self):
        order = ["G", "PG", "PG-13", "R", "NC-17"]
        items = [
            {"Id": "1", "OfficialRating": "UNRATED"},
            {"Id": "2"},
            {"Id": "3", "OfficialRating": "PG-13"},
            {"Id": "4", "OfficialRating": "R"},
        ]

        filtered = filter_items_by_official_rating_threshold(items, "R", order)

        self.assertEqual([x["Id"] for x in filtered], ["1", "2", "3"])

    def test_threshold_not_found_keeps_all_items(self):
        order = ["G", "PG", "PG-13"]
        items = [
            {"Id": "1", "OfficialRating": "PG"},
            {"Id": "2", "OfficialRating": "R"},
        ]

        filtered = filter_items_by_official_rating_threshold(items, "TV-14", order)

        self.assertEqual([x["Id"] for x in filtered], ["1", "2"])


if __name__ == "__main__":
    unittest.main()
