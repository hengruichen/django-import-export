import os.path
import warnings
from datetime import datetime
from io import BytesIO
from unittest import mock
from unittest.mock import MagicMock, patch

import chardet
import django
import tablib
from core.admin import AuthorAdmin, BookAdmin, CustomBookAdmin, ImportMixin
from core.models import Author, Book, Category, EBook, Parent
from django.contrib.admin.models import DELETION, LogEntry
from django.contrib.auth.models import User
from django.core.exceptions import PermissionDenied
from django.http import HttpRequest
from django.test.testcases import TestCase, TransactionTestCase
from django.test.utils import override_settings
from django.utils.translation import gettext_lazy as _
from openpyxl.reader.excel import load_workbook
from tablib import Dataset

from import_export import formats
from import_export.admin import (
    ExportActionMixin,
    ExportActionModelAdmin,
    ExportMixin,
    ImportExportActionModelAdmin,
)
from import_export.formats import base_formats
from import_export.formats.base_formats import DEFAULT_FORMATS
from import_export.tmp_storages import TempFolderStorage


class AdminTestMixin(object):
    category_change_url = "/admin/core/category/"
    book_import_url = "/admin/core/book/import/"
    book_process_import_url = "/admin/core/book/process_import/"
    legacybook_import_url = "/admin/core/legacybook/import/"
    legacybook_process_import_url = "/admin/core/legacybook/process_import/"
    child_import_url = "/admin/core/child/import/"
    child_process_import_url = "/admin/core/child/process_import/"

    def setUp(self):
        super().setUp()
        user = User.objects.create_user("admin", "admin@example.com", "password")
        user.is_staff = True
        user.is_superuser = True
        user.save()
        self.client.login(username="admin", password="password")

    def _do_import_post(
        self, url, filename, input_format=0, encoding=None, resource=None, follow=False
    ):
        input_format = input_format
        filename = os.path.join(
            os.path.dirname(__file__), os.path.pardir, "exports", filename
        )
        with open(filename, "rb") as f:
            data = {
                "input_format": str(input_format),
                "import_file": f,
            }
            if encoding:
                BookAdmin.from_encoding = encoding
            if resource:
                data.update({"resource": resource})
            response = self.client.post(url, data, follow=follow)
        return response

    def _assert_string_in_response(
        self,
        url,
        filename,
        input_format,
        encoding=None,
        str_in_response=None,
        follow=False,
        status_code=200,
    ):
        response = self._do_import_post(
            url, filename, input_format, encoding=encoding, follow=follow
        )
        self.assertEqual(response.status_code, status_code)
        self.assertTrue("result" in response.context)
        self.assertFalse(response.context["result"].has_errors())
        if str_in_response is not None:
            self.assertContains(response, str_in_response)

    def _get_input_format_index(self, format):
        for i, f in enumerate(DEFAULT_FORMATS):
            if f().get_title() == format:
                xlsx_index = i
                break
        else:
            raise Exception(
                "Unable to find %s format. DEFAULT_FORMATS: %r"
                % (format, DEFAULT_FORMATS)
            )
        return xlsx_index


class ImportAdminIntegrationTest(AdminTestMixin, TestCase):
    def test_import_export_template(self):
        response = self.client.get("/admin/core/book/")
        self.assertEqual(response.status_code, 200)
        self.assertTemplateUsed(
            response, "admin/import_export/change_list_import_export.html"
        )
        self.assertTemplateUsed(response, "admin/change_list.html")
        self.assertTemplateUsed(response, "core/admin/change_list.html")
        self.assertContains(response, "Import")
        self.assertContains(response, "Export")
        self.assertContains(response, "Import/Export")
        self.assertContains(response, "Import/Export")

    def test_import_action_success(self):
        # test that a successful import redirects to the import page
        self._is_regex_in_response(
            "books.csv",
            "0",
            "ISO-8859-1",
            follow=True,
            regex_in_response=(
                ".*Import finished, with 1 new and 0 updated .*"
                "books\\.?\\s*\\(See the .*"
            ),
        )
        self.assertEqual(1, Book.objects.count())

    def test_import_action_invalid_date(self):
        # test that a row with an invalid date redirects to errors page
        response = self._do_import_post(
            self.book_import_url, "books-invalid-date.csv", "0"
        )
        result = response.context["result"]
        # there should be a single invalid row
        self.assertEqual(1, len(result.invalid_rows))
        self.assertEqual(
            "Enter a valid date.", result.invalid_rows[0].error.messages[0]
        )
        # no rows should be imported because we rollback on validation errors
        self.assertEqual(0, Book.objects.count())

    def test_import_action_empty_author_email(self):
        xlsx_index = self._get_input_format_index("xlsx")
        # sqlite / MySQL / Postgres have different error messages
        self._is_regex_in_response(
            "books-empty-author-email.xlsx",
            xlsx_index,
            "ISO-8859-1",
            follow=True,
            regex_in_response=(
                ".*NOT NULL constraint failed: .*"
                "books\\.?\\s*author_email\\.?\\s*null value"
            ),
        )

    @override_settings(IMPORT_EXPORT_USE_TRANSACTIONS=True)
    def test_import_transaction_enabled_validation_error(self):
        # with transactions enabled, a validation error should cause the entire
        # import to be rolled back
        self._do_import_post(self.book_import_url, "books-invalid-date.csv")
        self.assertEqual(0, Book.objects.count())

    @override_settings(IMPORT_EXPORT_USE_TRANSACTIONS=False)
    def test_import_transaction_disabled_validation_error(self):
        # with transactions disabled, a validation error should not cause the entire
        # import to fail
        self._do_import_post(self.book_import_url, "books-invalid-date.csv")
        self.assertEqual(1, Book.objects.count())

    @override_settings(IMPORT_EXPORT_USE_TRANSACTIONS=True)
    def test_import_transaction_enabled_core_error(self):
        # with transactions enabled, a core error should cause the entire import to fail
        xlsx_index = self._get_input_format_index("xlsx")
        self._do_import_post(
            self.book_import_url, "books-empty-author-email.xlsx", xlsx_index
        )
        self.assertEqual(0, Book.objects.count())

    @override_settings(IMPORT_EXPORT_USE_TRANSACTIONS=False)
    def test_import_transaction_disabled_core_error(self):
        # with transactions disabled, a core (db contraint) error should not cause the
        # entire import to fail
        xlsx_index = self._get_input_format_index("xlsx")
        self._do_import_post(
            self.book_import_url, "books-empty-author-email.xlsx", xlsx_index
        )
        self.assertEqual(1, Book.objects.count())

    def test_import_action_mac(self):
        self._is_str_in_response(
            "books-mac.csv",
            "0",
            "ISO-8859-1",
            follow=True,
            str_in_response="Import finished, with 1 new and 0 updated books.",
        )

    def test_import_action_iso_8859_1(self):
        self._is_str_in_response(
            "books-ISO-8859-1.csv",
            "0",
            "ISO-8859-1",
            follow=True,
            str_in_response="Import finished, with 1 new and 0 updated books.",
        )

    def test_import_action_decode_error(self):
        # attempting to read a file with the incorrect encoding should raise an error
        self._is_regex_in_response(
            "books-ISO-8859-1.csv",
            "0",
            "ISO-8859-1",
            follow=True,
            encoding="utf-8-sig",
            regex_in_response=(
                ".*UnicodeDecodeError.* encountered " "while trying to read file"
            ),
        )

    def test_import_action_binary(self):
        self._is_str_in_response(
            "books.xls",
            "1",
            "ISO-8859-1",
            follow=True,
            str_in_response="Import finished, with 1 new and 0 updated books.",
        )

