"""Persistence and paper-identity regressions, all offline."""
import json
import unittest

import music_jepa_watch as m
from test_music_jepa_watch import meta, repo_client


class PersistenceAndIdentityTests(unittest.TestCase):
    def test_similarly_named_other_paper_is_ignored(self):
        watcher = m.Watcher(repo_client(readme="Music-JEPA: a different paper"), m.new_state())
        watcher.inspect_repo(meta("other/music-jepa"))
        self.assertFalse(watcher.events)
        self.assertFalse(watcher.state["observations"])

    def test_paper_catalogue_is_not_a_release(self):
        watcher = m.Watcher(repo_client(readme=f"Paper {m.PAPER}"), m.new_state())
        watcher.inspect_repo(meta("other/awesome-world-models"))
        self.assertFalse(watcher.events)
        watcher.client.json.assert_not_called()

    def test_serialized_release_state_does_not_repeat_email(self):
        client = repo_client(readme=f"Music-JEPA {m.PAPER}")
        client.pages.return_value = [{"tag_name": "v1", "html_url": "https://github.com/ZZWaang/new/releases/tag/v1",
                                     "assets": [{"name": "model.ckpt", "browser_download_url": "https://example.org/model.ckpt", "updated_at": "now"}]}]
        state = m.new_state()
        m.Watcher(client, state).inspect_repo(meta("ZZWaang/new"))
        state = json.loads(json.dumps(state))
        watcher = m.Watcher(client, state)
        watcher.inspect_repo(meta("ZZWaang/new"))
        self.assertFalse(watcher.events)


if __name__ == "__main__":
    unittest.main()
