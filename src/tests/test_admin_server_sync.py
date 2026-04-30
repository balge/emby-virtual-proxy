import unittest

from library_binding_sync import prune_source_library_ids, sync_real_library_configs
from models import RealLibraryConfig


class TestAdminServerSync(unittest.TestCase):
    def test_sync_real_library_configs_drops_deleted_and_preserves_flags(self):
        existing = [
            RealLibraryConfig(id="lib-a", name="Old A", enabled=False, cover_enabled=False),
            RealLibraryConfig(id="lib-b", name="Old B", enabled=True),
        ]
        emby_libs = [
            {"Id": "lib-a", "Name": "New A"},
            {"Id": "lib-c", "Name": "New C"},
        ]

        synced = sync_real_library_configs(existing, emby_libs)

        self.assertEqual([x.id for x in synced], ["lib-a", "lib-c"])
        self.assertEqual(synced[0].name, "New A")
        self.assertFalse(synced[0].enabled)
        self.assertFalse(synced[0].cover_enabled)
        self.assertTrue(synced[1].enabled)

    def test_prune_source_library_ids_removes_deleted_and_disabled(self):
        pruned = prune_source_library_ids(
            ["lib-a", "lib-b", "lib-c", "lib-b", ""],
            valid_real_ids={"lib-a", "lib-b"},
            disabled_real_ids={"lib-b"},
        )

        self.assertEqual(pruned, ["lib-a"])


if __name__ == "__main__":
    unittest.main()
