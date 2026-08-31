import unittest

from sl_office.students.excel import InvalidWorkbook, import_students, safe_excel_value


class ExcelSecurityTests(unittest.TestCase):
    def test_formula_prefixes_are_escaped(self):
        for value in ("=1+1", "+cmd", "-2+3", "@SUM(A1:A2)", "  =hidden"):
            self.assertTrue(safe_excel_value(value).startswith("'"))
        self.assertEqual(safe_excel_value("Normaler Name"), "Normaler Name")

    def test_non_xlsx_payload_is_rejected_before_parser(self):
        with self.assertRaises(InvalidWorkbook):
            import_students(b"not an excel workbook")


if __name__ == "__main__":
    unittest.main()
