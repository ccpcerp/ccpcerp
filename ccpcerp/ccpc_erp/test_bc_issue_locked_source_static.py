"""Synthetic SQL-boundary checks; no Frappe, DB connection or posting."""
import unittest
from ccpcerp.ccpc_erp.bc_mapping_policy import MappingPolicyError
from ccpcerp.ccpc_erp.bc_issue_locked_source import read_locked_issue_item_groups


class FakeDB:
    def __init__(self, *, department=None, items=None, groups=None):
        self.calls=[]
        self.department=department if department is not None else [
            dict(name="_Test Maintenance",company="_Test Company",is_group=0,disabled=0)]
        self.items=items if items is not None else [
            dict(name="PART-1",item_group="_Test Materials",disabled=0,is_stock_item=1)]
        self.groups=groups if groups is not None else [dict(name="_Test Materials",is_group=0)]

    def sql(self, query, params, *, as_dict):
        self.calls.append((query,params,as_dict))
        assert as_dict is True
        assert "FOR UPDATE" in query
        assert "%s" in query and "PART-1" not in query and "_Test Maintenance" not in query
        if "`tabDepartment`" in query:return self.department
        if "`tabItem`" in query:return self.items
        if "`tabItem Group`" in query:return self.groups
        raise AssertionError("Unexpected SQL source")


def read(db=None,*,rows=None,company="_Test Company"):
    db=db or FakeDB()
    return read_locked_issue_item_groups(db,company=company,beneficiary_department="_Test Maintenance",
             slip_rows=rows if rows is not None else [dict(source_row="WSI-1",item_code="PART-1")])


class LockedSourceTests(unittest.TestCase):
    def test_locked_snapshot_uses_canonical_item_group_not_client_value(self):
        db=FakeDB()
        got=read(db,rows=[dict(source_row="WSI-1",item_code="PART-1",item_group="FORGED")])
        self.assertEqual((got[0].source_row,got[0].item_code,got[0].item_group),
                         ("WSI-1","PART-1","_Test Materials"))
        self.assertEqual(len(db.calls),3)
        self.assertEqual(db.calls[1][1],("PART-1",))
        self.assertEqual(db.calls[2][1],("_Test Materials",))

    def test_wrong_company_and_department_ineligible(self):
        for db,company in (
            (FakeDB(),"Other Company"),
            (FakeDB(department=[]),"_Test Company"),
            (FakeDB(department=[dict(name="_Test Maintenance",company="_Test Company",is_group=1,disabled=0)]),"_Test Company"),
            (FakeDB(department=[dict(name="_Test Maintenance",company="_Test Company",is_group=0,disabled=1)]),"_Test Company"),
        ):
            with self.subTest(db=db.department,company=company),self.assertRaises(MappingPolicyError):
                read(db,company=company)

    def test_disabled_nonstock_unknown_or_duplicate_item_fails(self):
        for item in (
            dict(name="PART-1",item_group="_Test Materials",disabled=1,is_stock_item=1),
            dict(name="PART-1",item_group="_Test Materials",disabled=0,is_stock_item=0),
            dict(name="PART-1",item_group="",disabled=0,is_stock_item=1),
            dict(name="OTHER",item_group="_Test Materials",disabled=0,is_stock_item=1),
        ):
            with self.subTest(item=item), self.assertRaises(MappingPolicyError):
                read(FakeDB(items=[item]))
        with self.assertRaises(MappingPolicyError):read(FakeDB(items=[]))
        with self.assertRaises(MappingPolicyError):read(FakeDB(items=FakeDB().items * 2))

    def test_missing_parent_or_duplicate_leaf_group_fails(self):
        for groups in ([],[dict(name="_Test Materials",is_group=1)],
                       [dict(name="OTHER",is_group=0)],FakeDB().groups*2):
            with self.subTest(groups=groups),self.assertRaises(MappingPolicyError):
                read(FakeDB(groups=groups))

    def test_noncanonical_duplicate_or_unbounded_slip_rows_denied_pre_sql(self):
        for rows in ([], [dict(source_row="WSI-1",item_code="PART-1")]*2,
                     [dict(source_row="",item_code="PART-1")],
                     [dict(source_row="WSI-1",item_code="")], ["forged"],
                     [dict(source_row="S"+str(i),item_code="PART-1") for i in range(1001)]):
            db=FakeDB()
            with self.subTest(n=len(rows)),self.assertRaises(MappingPolicyError):read(db,rows=rows)
            self.assertEqual(db.calls,[])

    def test_multiple_item_codes_lock_in_stable_name_order(self):
        db=FakeDB(items=[dict(name="A",item_group="_Test Materials",disabled=0,is_stock_item=1),
                         dict(name="Z",item_group="_Test Materials",disabled=0,is_stock_item=1)])
        out=read(db,rows=[dict(source_row="WSI-Z",item_code="Z"),dict(source_row="WSI-A",item_code="A")])
        self.assertEqual(db.calls[1][1],("A","Z"))
        self.assertEqual([r.item_code for r in out],["Z","A"])

if __name__ == "__main__":unittest.main()
