import json
import sys
from collections import OrderedDict
from copy import deepcopy
from datetime import date
from decimal import Decimal, InvalidOperation
from unittest import mock, skipUnless
from unittest.mock import patch

import tablib
from core.models import (
    Author,
    Book,
    Category,
    Entry,
    Profile,
    WithDynamicDefault,
    WithFloatField,
)
from core.tests.resources import (
    AuthorResource,
    AuthorResourceWithCustomWidget,
    BookResource,
    BookResourceWithLineNumberLogger,
    BookResourceWithStoreInstance,
    CategoryResource,
    MyResource,
    ProfileResource,
    WithDefaultResource,
)
from core.tests.utils import ignore_widget_deprecation_warning
from django.conf import settings
from django.contrib.auth.models import User
from django.core.exceptions import (
    FieldDoesNotExist,
    ImproperlyConfigured,
    ValidationError,
)
from django.core.paginator import Paginator
from django.db import IntegrityError
from django.db.utils import ConnectionDoesNotExist
from django.test import TestCase, TransactionTestCase, skipUnlessDBFeature
from django.utils.encoding import force_str
from django.utils.html import strip_tags

from import_export import exceptions, fields, resources, results, widgets
from import_export.instance_loaders import ModelInstanceLoader
from import_export.options import ResourceOptions
from import_export.resources import Diff


class ResourceTestCase(TestCase):
    def setUp(self):
        self.my_resource = MyResource()

    def test_fields(self):
        """Check that fields were determined correctly"""

        # check that our fields were determined
        self.assertIn("name", self.my_resource.fields)

        # check that resource instance fields attr isn't link to resource cls
        # fields
        self.assertFalse(MyResource.fields is self.my_resource.fields)

        # dynamically add new resource field into resource instance
        self.my_resource.fields.update(
            OrderedDict(
                [
                    ("new_field", fields.Field()),
                ]
            )
        )

        # check that new field in resource instance fields
        self.assertIn("new_field", self.my_resource.fields)

        # check that new field not in resource cls fields
        self.assertNotIn("new_field", MyResource.fields)

    def test_kwargs(self):
        target_kwargs = {"a": 1}
        my_resource = MyResource(**target_kwargs)
        self.assertEqual(my_resource.kwargs, target_kwargs)

    def test_field_column_name(self):
        field = self.my_resource.fields["name"]
        self.assertIn(field.column_name, "name")

    def test_meta(self):
        self.assertIsInstance(self.my_resource._meta, ResourceOptions)

    @mock.patch("builtins.dir")
    def test_new_handles_null_options(self, mock_dir):
        # #1163 - simulates a call to dir() returning additional attributes
        mock_dir.return_value = ["attrs"]

        class A(MyResource):
            pass

        A()

    def test_get_export_headers_order(self):
        self.assertEqual(
            self.my_resource.get_export_headers(), ["email", "name", "extra"]
        )

    def test_default_after_import(self):
        self.assertIsNone(
            self.my_resource.after_import(
                tablib.Dataset(),
                results.Result(),
            )
        )

    def test_get_use_transactions_defined_in_resource(self):
        class A(MyResource):
            class Meta:
                use_transactions = True

        resource = A()
        self.assertTrue(resource.get_use_transactions())

    def test_get_field_name_raises_AttributeError(self):
        err = (
            "Field x does not exists in <class "
            "'core.tests.resources.MyResource'> resource"
        )
        with self.assertRaisesRegex(AttributeError, err):
            self.my_resource.get_field_name("x")

    def test_init_instance_raises_NotI
# ... [truncated] ...
ource = resources.modelresource_factory(
                model=BookWithChapters
            )()
            result = book_with_chapters_resource.import_data(dataset, dry_run=False)

            self.assertFalse(result.has_errors())
            book_with_chapters = list(BookWithChapters.objects.all())[0]
            self.assertListEqual(book_with_chapters.chapters, chapters)

    class TestImportArrayField(TestCase):
        def setUp(self):
            self.resource = BookWithChaptersResource()
            self.chapters = ["Introduction", "Middle Chapter", "Ending"]
            self.book = BookWithChapters.objects.create(name="foo")
            self.dataset = tablib.Dataset(headers=["id", "name", "chapters"])
            row = [self.book.id, "Some book", ",".join(self.chapters)]
            self.dataset.append(row)

        @ignore_widget_deprecation_warning
        def test_import_of_data_with_array(self):
            self.assertListEqual(self.book.chapters, [])
            result = self.resource.import_data(self.dataset, raise_errors=True)

            self.assertFalse(result.has_errors())
            self.assertEqual(len(result.rows), 1)

            self.book.refresh_from_db()
            self.assertEqual(self.book.chapters, self.chapters)

    class TestImportIntArrayField(TestCase):
        def setUp(self):
            self.resource = BookWithChapterNumbersResource()
            self.chapter_numbers = [1, 2, 3]
            self.book = BookWithChapterNumbers.objects.create(
                name="foo", chapter_numbers=[]
            )
            self.dataset = tablib.Dataset(
                *[(1, "some book", "1,2,3")], headers=["id", "name", "chapter_numbers"]
            )

        @ignore_widget_deprecation_warning
        def test_import_of_data_with_int_array(self):
            # issue #1495
            self.assertListEqual(self.book.chapter_numbers, [])
            result = self.resource.import_data(self.dataset, raise_errors=True)

            self.assertFalse(result.has_errors())
            self.assertEqual(len(result.rows), 1)

            self.book.refresh_from_db()
            self.assertEqual(self.book.chapter_numbers, self.chapter_numbers)

    class TestExportJsonField(TestCase):
        def setUp(self):
            self.json_data = {"some_key": "some_value"}
            self.book = BookWithChapters.objects.create(name="foo", data=self.json_data)

        @ignore_widget_deprecation_warning
        def test_export_field_with_appropriate_format(self):
            resource = resources.modelresource_factory(model=BookWithChapters)()
            result = resource.export(BookWithChapters.objects.all())

            assert result[0][3] == json.dumps(self.json_data)

    class TestImportJsonField(TestCase):
        def setUp(self):
            self.resource = BookWithChaptersResource()
            self.data = {"some_key": "some_value"}
            self.json_data = json.dumps(self.data)
            self.book = BookWithChapters.objects.create(name="foo")
            self.dataset = tablib.Dataset(headers=["id", "name", "data"])
            row = [self.book.id, "Some book", self.json_data]
            self.dataset.append(row)

        @ignore_widget_deprecation_warning
        def test_sets_json_data_when_model_field_is_empty(self):
            self.assertIsNone(self.book.data)
            result = self.resource.import_data(self.dataset, raise_errors=True)

            self.assertFalse(result.has_errors())
            self.assertEqual(len(result.rows), 1)

            self.book.refresh_from_db()
            self.assertEqual(self.book.data, self.data)


class BookResourceWithStringModelTest(TestCase):
    def setUp(self):
        class BookResourceWithStringModel(resources.ModelResource):
            class Meta:
                model = "core.Book"

        self.resource = BookResourceWithStringModel()

    def test_resource_gets_correct_model_from_string(self):
        self.assertEqual(self.resource._meta.model, Book)

