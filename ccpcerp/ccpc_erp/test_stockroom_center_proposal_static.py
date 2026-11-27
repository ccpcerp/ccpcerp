"""Pure, non-site regression for the exact Stockroom Center-only maker exception.

Executes only extracted authorization functions with a fake locked DB. Does
NOT import Frappe, create real records, approve anything or post stock/GL.
A disposable Frappe/MariaDB test separately covers the full approval path.
"""
import ast
from pathlib import Path
import types
import unittest


HERE = Path(__file__).resolve().parent
RUNTIME = HERE / "bc_issue_runtime.py"
WORKFLOW = HERE / "bc_mapping_workflow.py"
EVIDENCE = HERE / "bc_finance_evidence.py"
ONBOARDING = HERE / "phase1_finance_onboarding.py"
PAGE = HERE / "page" / "ccpc_phase1_finance" / "ccpc_phase1_finance.js"

CCPC = "Cagayan Corn Products Corporation"
MAIN = "Main - CCPC"
MAKER = "ccpc-test-stockroom@example.invalid"
CONTROLLER = "ccpc-test-controller@example.invalid"


class Denied(ValueError):
    pass


class LockedFakeDB:
    def __init__(self):
        self.roles = {
            MAKER: {"Stockroom Personnel", "CCPC Mapping Preparer"},
            CONTROLLER: {"CCPC Accounting Mapping Approver"},
        }
        self.grants = {MAKER: {CCPC}, CONTROLLER: {CCPC}}
        self.enabled = {MAKER: 1, CONTROLLER: 1}
        self.reads = []

    def sql(self, statement, args, as_dict=False):
        assert as_dict is True
        assert "FOR UPDATE" in statement
        self.reads.append((statement, args))
        if "FROM " + chr(96) + "tabUser" + chr(96) in statement:
            user, = args
            return [{"enabled": self.enabled[user]}] if user in self.enabled else []
        if "FROM " + chr(96) + "tabHas Role" + chr(96) in statement:
            user, role = args
            return [{"name": "real-role"}] if role in self.roles.get(user, set()) else []
        if "FROM " + chr(96) + "tabUser Permission" + chr(96) in statement:
            user, company = args
            return [{"name": "real-grant"}] if company in self.grants.get(user, set()) else []
        raise AssertionError("Unexpected query: " + statement)


def extracted_runtime(db):
    source = ast.parse(RUNTIME.read_text())
    names = {
        "_require", "_canonical", "_finance_role_company_authority",
        "_has_locked_mapping_role", "_real_authority",
        "_center_proposal_maker_authority",
    }
    functions = [node for node in source.body
                 if isinstance(node, ast.FunctionDef) and node.name in names]
    assert {f.name for f in functions} == names
    constants = [node for node in source.body if isinstance(node, ast.Assign)
                 and any(isinstance(t, ast.Name)
                         and t.id in ("STOCKROOM_CENTER_PROPOSAL_COMPANY",
                                     "STOCKROOM_CENTER_PROPOSAL_VALUE")
                         for t in node.targets)]
    assert len(constants) == 2
    module = ast.fix_missing_locations(ast.Module(body=constants + functions, type_ignores=[]))
    namespace = {
        "MappingPolicyError": Denied,
        "frappe": types.SimpleNamespace(db=db),
    }
    exec(compile(module, str(RUNTIME), "exec"), namespace)
    return namespace


def direct_calls(path, function):
    tree = ast.parse(path.read_text())
    node = next(n for n in tree.body
                if isinstance(n, ast.FunctionDef) and n.name == function)
    return {n.func.id for n in ast.walk(node)
            if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}


class StockroomCenterMakerPureTests(unittest.TestCase):
    def setUp(self):
        self.db = LockedFakeDB()
        self.auth = extracted_runtime(self.db)
        self.maker = self.auth["_center_proposal_maker_authority"]
        self.strict = self.auth["_real_authority"]

    def test_stockroom_can_only_propose_ccpc_main_center(self):
        self.maker(MAKER, "Center", CCPC, MAIN)
        self.assertTrue(self.db.reads)
        self.assertTrue(all("FOR UPDATE" in q for q, _ in self.db.reads))

    def test_context_read_may_have_no_target_but_write_must_supply_target(self):
        self.maker(MAKER, "Center", CCPC)
        self.maker(MAKER, "Center", CCPC, MAIN)

    def test_stockroom_expense_proposal_denied(self):
        with self.assertRaises(Denied):
            self.maker(MAKER, "Expense", CCPC, MAIN)

    def test_stockroom_other_company_denied(self):
        with self.assertRaises(Denied):
            self.maker(MAKER, "Center", "Other Company", MAIN)

    def test_stockroom_other_center_denied(self):
        with self.assertRaises(Denied):
            self.maker(MAKER, "Center", CCPC, "Other - CCPC")

    def test_stockroom_requires_preparer_role(self):
        self.db.roles[MAKER].remove("CCPC Mapping Preparer")
        with self.assertRaises(Denied):
            self.maker(MAKER, "Center", CCPC, MAIN)

    def test_stockroom_requires_company_grant(self):
        self.db.grants[MAKER].clear()
        with self.assertRaises(Denied):
            self.maker(MAKER, "Center", CCPC, MAIN)

    def test_stockroom_requires_enabled_named_user(self):
        self.db.enabled[MAKER] = 0
        with self.assertRaises(Denied):
            self.maker(MAKER, "Center", CCPC, MAIN)
        with self.assertRaises(Denied):
            self.maker("Administrator", "Center", CCPC, MAIN)

    def test_stockroom_with_approver_role_denied_even_for_proposals(self):
        self.db.roles[MAKER].add("CCPC Accounting Mapping Approver")
        with self.assertRaises(Denied):
            self.maker(MAKER, "Center", CCPC, MAIN)

    def test_stockroom_never_passes_strict_checker_check(self):
        self.db.roles[MAKER].add("CCPC Accounting Mapping Approver")
        with self.assertRaises(Denied):
            self.strict(MAKER, "CCPC Accounting Mapping Approver", CCPC)

    def test_finance_only_preparer_keeps_legacy_expense_authority(self):
        self.db.roles[CONTROLLER].add("CCPC Mapping Preparer")
        self.maker(CONTROLLER, "Expense", CCPC, "Signed-expense-account")
        self.strict(CONTROLLER, "CCPC Accounting Mapping Approver", CCPC)

    def test_missing_finance_role_denied(self):
        self.db.roles[MAKER] = {"Stockroom Personnel"}
        with self.assertRaises(Denied):
            self.maker(MAKER, "Center", CCPC, MAIN)


class StockroomWiringStaticTests(unittest.TestCase):
    def test_post_and_sign_paths_revalidate_actual_maker(self):
        self.assertIn("_center_proposal_maker_authority",
                      direct_calls(WORKFLOW, "propose_mapping"))
        self.assertIn("_center_proposal_maker_authority",
                      direct_calls(WORKFLOW, "approve_mapping"))
        self.assertIn("_center_proposal_maker_authority",
                      direct_calls(EVIDENCE, "sign_mapping_decision"))
        self.assertIn("_real_authority", direct_calls(WORKFLOW, "approve_mapping"))
        self.assertIn("_real_authority", direct_calls(WORKFLOW, "get_mapping_review"))

    def test_finance_onboarding_gates_all_stockroom_routes(self):
        src = ONBOARDING.read_text()
        funcs = ("_actor_roles", "_stockroom_actor", "_authorize",
                 "get_pilot_context", "search_pilot_master",
                 "get_pilot_coverage", "propose_pilot_mapping")
        tree = ast.parse(src)
        self.assertTrue(all(any(isinstance(n, ast.FunctionDef) and n.name == k
                                for n in tree.body) for k in funcs))
        self.assertIn('_center_proposal_maker_authority(frappe.session.user, kind, company, value)', src)
        self.assertIn('roles == {PREPARER}', src)
        self.assertIn('if _stockroom_actor() and field == "value":', src)
        self.assertIn('["Center"] if _stockroom_actor()', src)
        self.assertIn('_authorize(company, kind)', src)

    def test_existing_lifecycle_and_checker_paths_remain_strict(self):
        lifecycle = (HERE / "bc_mapping_lifecycle.py").read_text()
        for needle in ('_real_authority(', '"CCPC Accounting Mapping Approver"'):
            self.assertIn(needle, lifecycle)
        evidence = EVIDENCE.read_text()
        self.assertIn('kind, revision, "CCPC Accounting Mapping Approver"', evidence)
        self.assertIn('assert_independent_review(row.owner, checker)', evidence)
        self.assertIn('_real_authority(checker, "CCPC Accounting Mapping Approver", company)',
                      WORKFLOW.read_text())

    def test_page_restricts_visible_kinds_without_trusting_client(self):
        text = PAGE.read_text()
        self.assertIn('access.allowed_kinds', text)
        self.assertIn('allowedKinds.includes("Expense")', text)
        self.assertIn('allowedKinds.includes("Center")', text)
        self.assertIn('propose_pilot_mapping', text)
        self.assertIn('sign_and_approve_initial_mapping', text)


if __name__ == "__main__":
    unittest.main()
