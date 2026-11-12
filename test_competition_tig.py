import base64
import copy
import unittest

from competition_tig import collect_tig


API = "https://mainnet-api.tig.foundation"
GH = "https://api.github.com/repos/tig-foundation/tig-monorepo"


def code(name="solver", code_id="c001_a001", **state_overrides):
    state = {"round_submitted": 4, "round_pushed": 6, "round_active": 8,
             "round_merged": None, "banned": False, "block_confirmed": 40}
    state.update(state_overrides)
    return {"id": code_id, "details": {"name": name, "challenge_id": "c001", "player_id": "player"},
            "state": state, "block_data": {"adoption": "100000000000000000", "merge_points": 2}}


class FakeClient:
    def __init__(self):
        self.calls = []
        self.block = {"id": "block-100", "details": {"height": 100, "round": 8, "timestamp": 12345}}
        self.codes = [code()]
        self.advances = None
        self.branches = [{"name": "satisfiability/solver", "commit": {"sha": "head-one"}}]
        self.trees = {}
        self.blobs = {}
        self.fail_heads = set()
        self.add_source("solver", "head-one", "blob-one", "fn solve() { old(); }\n")

    def add_source(self, name, head, blob, content):
        path = f"tig-algorithms/src/satisfiability/{name}/mod.rs"
        self.trees[head] = {"truncated": False, "tree": [
            {"path": path, "type": "blob", "sha": blob, "size": len(content.encode())},
            {"path": "tig-algorithms/src/satisfiability/other/mod.rs", "type": "blob", "sha": "other", "size": 1}]}
        self.blobs[blob] = {"encoding": "base64", "content": base64.b64encode(content.encode()).decode()}

    def json(self, url, params=None):
        self.calls.append((url, params))
        if url == f"{API}/get-block":
            return {"block": copy.deepcopy(self.block)}
        if url in {f"{API}/get-challenges", f"{API}/get-algorithms"}:
            assert params == {"block_id": self.block["id"]}, "API snapshot was not pinned"
        if url == f"{API}/get-challenges":
            return {"challenges": [{"id": "c001", "config": {"name": "satisfiability"}}]}
        if url == f"{API}/get-algorithms":
            result = {"codes": copy.deepcopy(self.codes), "binarys": [
                {"algorithm_id": c["id"], "details": {"compile_success": True}} for c in self.codes]}
            if self.advances is not None:
                result["advances"] = copy.deepcopy(self.advances)
            return result
        if url == f"{GH}/branches":
            start = (params["page"] - 1) * params["per_page"]
            return copy.deepcopy(self.branches[start:start + params["per_page"]])
        if url.startswith(f"{GH}/git/trees/"):
            head = url.rsplit("/", 1)[-1]
            if head in self.fail_heads:
                raise RuntimeError("temporary failure")
            return copy.deepcopy(self.trees[head])
        if url.startswith(f"{GH}/git/blobs/"):
            return copy.deepcopy(self.blobs[url.rsplit("/", 1)[-1]])
        raise AssertionError(f"Unexpected request: {url}, {params}")


class TigTests(unittest.TestCase):
    def test_baseline_then_unchanged_does_not_download_source_again(self):
        client = FakeClient()
        first = collect_tig({}, None, client)
        self.assertEqual(first["events"], [])
        self.assertEqual(first["errors"], [])
        self.assertEqual(first["stats"]["source_checks"], 0)
        self.assertEqual(first["state"]["sources"], {})
        self.assertEqual(first["stats"]["baseline_content_uninspected"], 1)
        client.calls.clear()
        again = collect_tig({}, first["state"], client)
        self.assertEqual(again["events"], [])
        self.assertEqual(again["stats"]["source_checks"], 0)
        self.assertFalse(any("/git/trees/" in url for url, _ in client.calls))

    def test_source_content_changes_and_bounded_diff(self):
        client = FakeClient()
        first = collect_tig({}, None, client)
        client.branches[0]["commit"]["sha"] = "head-two"
        client.add_source("solver", "head-two", "blob-two", "fn solve() { new(); }\n")
        result = collect_tig({}, first["state"], client)
        event = next(e for e in result["events"] if e["kind"] == "source")
        self.assertIn("-fn solve() { old(); }", event["detail"])
        self.assertIn("+fn solve() { new(); }", event["detail"])
        self.assertIn("/tree/head-two/", event["url"])
        self.assertEqual(event["metadata"]["previous_head"], "head-one")

    def test_unrelated_branch_commit_is_not_source_change(self):
        client = FakeClient()
        first = collect_tig({}, None, client)
        client.branches[0]["commit"]["sha"] = "head-two"
        client.trees["head-two"] = copy.deepcopy(client.trees["head-one"])
        result = collect_tig({}, first["state"], client)
        self.assertEqual(result["events"], [])
        self.assertEqual(result["state"]["sources"]["satisfiability/solver"]["head"], "head-two")

    def test_source_failures_preserve_successful_snapshot_and_retry(self):
        client = FakeClient()
        first = collect_tig({}, None, client)
        client.branches[0]["commit"]["sha"] = "head-two"
        client.add_source("solver", "head-two", "blob-two", "new source")
        client.fail_heads.add("head-two")
        failed = collect_tig({}, first["state"], client)
        self.assertEqual(failed["state"]["sources"], first["state"]["sources"])
        self.assertEqual(failed["state"]["pending_sources"], ["satisfiability/solver"])
        self.assertTrue(failed["errors"])
        client.fail_heads.clear()
        retry = collect_tig({}, failed["state"], client)
        self.assertEqual(len(retry["events"]), 1)
        self.assertEqual(retry["stats"]["pending_sources"], 0)

    def test_baseline_inventory_does_not_make_historical_backlog(self):
        client = FakeClient()
        client.branches.append({"name": "satisfiability/zsolver", "commit": {"sha": "head-z"}})
        client.add_source("zsolver", "head-z", "blob-z", "source z")
        first = collect_tig({"max_source_checks": 1}, None, client)
        self.assertEqual(first["stats"]["pending_sources"], 0)
        self.assertEqual(first["stats"]["baseline_content_uninspected"], 2)
        self.assertFalse(first["errors"])
        again = collect_tig({"max_source_checks": 1}, first["state"], client)
        self.assertEqual(again["stats"]["pending_sources"], 0)
        self.assertEqual(again["events"], [])

    def test_uninspected_baseline_branch_change_loads_previous_commit(self):
        client = FakeClient()
        client.branches.append({"name": "satisfiability/zsolver", "commit": {"sha": "head-z"}})
        client.add_source("zsolver", "head-z", "blob-z", "source z")
        first = collect_tig({"max_source_checks": 1}, None, client)
        client.branches[1]["commit"]["sha"] = "head-z-new"
        client.add_source("zsolver", "head-z-new", "blob-z-new", "new source z")
        again = collect_tig({"max_source_checks": 1}, first["state"], client)
        self.assertEqual(len(again["events"]), 1)
        self.assertEqual(again["events"][0]["metadata"]["previous_head"], "head-z")
        self.assertIn((f"{GH}/git/trees/head-z", {"recursive": 1}), client.calls)

    def test_failed_branch_does_not_starve_backlog(self):
        client = FakeClient()
        first = collect_tig({"max_source_checks": 1}, None, client)
        client.branches[0]["commit"]["sha"] = "head-bad"
        client.fail_heads.add("head-bad")
        client.branches.append({"name": "satisfiability/zsolver", "commit": {"sha": "head-z"}})
        client.add_source("zsolver", "head-z", "blob-z", "source z")
        failed = collect_tig({"max_source_checks": 1}, first["state"], client)
        again = collect_tig({"max_source_checks": 1}, failed["state"], client)
        self.assertIn("satisfiability/zsolver", again["state"]["sources"])
        self.assertEqual(again["state"]["pending_sources"], ["satisfiability/solver"])

    def test_daily_cap_retains_unprocessed_new_branch_for_next_run(self):
        client = FakeClient()
        first = collect_tig({"max_source_checks": 1}, None, client)
        for name in ("aa", "bb"):
            client.branches.append({"name": f"satisfiability/{name}", "commit": {"sha": f"head-{name}"}})
            client.add_source(name, f"head-{name}", f"blob-{name}", f"source {name}")
        capped = collect_tig({"max_source_checks": 1}, first["state"], client)
        self.assertEqual(capped["stats"]["pending_sources"], 1)
        self.assertEqual(len(capped["events"]), 1)
        self.assertNotIn("satisfiability/bb", capped["state"]["sources"])
        later = collect_tig({"max_source_checks": 1}, capped["state"], client)
        self.assertEqual(later["stats"]["pending_sources"], 0)
        self.assertEqual(later["events"][0]["metadata"]["branch"], "satisfiability/bb")

    def test_complete_pagination_and_limit_failure(self):
        client = FakeClient()
        client.branches = [{"name": f"dev/{i}", "commit": {"sha": f"sha{i}"}} for i in range(100)] + client.branches
        result = collect_tig({}, None, client)
        self.assertEqual(result["stats"]["public_branches"], 1)
        self.assertTrue(any(params == {"per_page": 100, "page": 2} for _, params in client.calls))
        with self.assertRaisesRegex(ValueError, "max_branch_pages"):
            collect_tig({"max_branch_pages": 1}, None, client)

    def test_truncated_tree_and_file_cap_leave_branch_pending(self):
        client = FakeClient()
        first = collect_tig({}, None, client)
        client.trees["head-one"]["truncated"] = True
        client.branches[0]["commit"]["sha"] = "head-two"
        result = collect_tig({}, first["state"], client)
        self.assertEqual(result["state"]["sources"], {})
        self.assertEqual(result["stats"]["pending_sources"], 1)
        self.assertIn("truncated", result["errors"][0])

    def test_oversized_source_keeps_hash_but_explicitly_omits_text(self):
        client = FakeClient()
        first = collect_tig({}, None, client)
        client.branches.append({"name": "satisfiability/big", "commit": {"sha": "head-big"}})
        client.add_source("big", "head-big", "blob-big", "x" * 101)
        result = collect_tig({"max_file_bytes": 100}, first["state"], client)
        snapshot = result["state"]["sources"]["satisfiability/big"]
        self.assertEqual(len(snapshot["files"]), 1)
        self.assertEqual(snapshot["excerpts"], {})
        self.assertIn("oversized", result["events"][0]["detail"])
        self.assertFalse(any(url.endswith("/git/blobs/blob-big") for url, _ in client.calls))

    def test_advance_evidence_is_tracked_without_algorithm_directory(self):
        client = FakeClient()
        first = collect_tig({}, None, client)
        client.branches.append({"name": "satisfiability/method", "commit": {"sha": "head-method"}})
        path = "tig-algorithms/advances/satisfiability/method.md"
        client.trees["head-method"] = {"truncated": False, "tree": [
            {"type": "blob", "path": path, "sha": "evidence", "size": 8}]}
        client.blobs["evidence"] = {"encoding": "base64", "content": base64.b64encode(b"evidence").decode()}
        result = collect_tig({}, first["state"], client)
        self.assertIn(path, result["events"][0]["metadata"]["changed_files"])

    def test_adoption_fixed_point_and_stage_changes(self):
        client = FakeClient()
        first = collect_tig({"depth": "light"}, None, client)
        self.assertEqual(first["state"]["records"]["code:c001_a001"]["adoption"], 0.1)
        client.codes[0]["block_data"]["adoption"] = "170000000000000000"
        client.codes[0]["state"]["round_merged"] = 8
        result = collect_tig({"depth": "light"}, first["state"], client)
        self.assertEqual(len(result["events"]), 1)
        self.assertIn("10.00% → 17.00%", result["events"][0]["detail"])
        self.assertIn("active → merged", result["events"][0]["detail"])
        self.assertFalse(any(url.startswith(GH) for url, _ in client.calls))

    def test_new_private_submission_not_claimed_to_have_public_code(self):
        client = FakeClient()
        first = collect_tig({"depth": "light"}, None, client)
        client.codes.append(code("new", "c001_a002", round_pushed=10, round_active=12))
        result = collect_tig({"depth": "light"}, first["state"], client)
        self.assertEqual(result["events"][0]["metadata"]["public"], False)
        self.assertIn("docs.tig.foundation", result["events"][0]["url"])

    def test_missing_rows_do_not_forget_historical_submissions(self):
        client = FakeClient()
        first = collect_tig({"depth": "light"}, None, client)
        client.codes = []
        result = collect_tig({"depth": "light"}, first["state"], client)
        self.assertIn("code:c001_a001", result["state"]["records"])
        self.assertEqual(result["events"], [])

    def test_regressing_block_rejected(self):
        client = FakeClient()
        first = collect_tig({"depth": "light"}, None, client)
        client.block["details"]["height"] = 99
        with self.assertRaisesRegex(ValueError, "regressed"):
            collect_tig({"depth": "light"}, first["state"], client)

    def test_standard_depth_never_requests_blob_content(self):
        client = FakeClient()
        first = collect_tig({"depth": "standard"}, None, client)
        client.branches[0]["commit"]["sha"] = "head-two"
        client.add_source("solver", "head-two", "blob-two", "new source")
        result = collect_tig({"depth": "standard"}, first["state"], client)
        self.assertFalse(any("/git/blobs/" in url for url, _ in client.calls))
        self.assertTrue(result["state"]["sources"]["satisfiability/solver"]["files"])

    def test_required_schema_failure_is_not_a_quiet_no_update(self):
        client = FakeClient()
        client.codes = "wrong shape"
        with self.assertRaises((ValueError, TypeError)):
            collect_tig({}, None, client)


if __name__ == "__main__":
    unittest.main()
