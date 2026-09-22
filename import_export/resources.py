import functools
import logging
import traceback
from collections import OrderedDict
from copy import deepcopy
from html import escape

import tablib
from diff_match_patch import diff_match_patch
from django.apps import apps
from django.conf import settings
from django.core.exceptions import (
    FieldDoesNotExist,
    ImproperlyConfigured,
    ValidationError,
)
from django.core.management.color import no_style
from django.core.paginator import Paginator
from django.db import connections, router
from django.db.models import fields
from django.db.models.fields.related import ForeignObjectRel
from django.db.models.query import QuerySet
from django.db.transaction import TransactionManagementError, set_rollback
from django.utils.encoding import force_str
from django.utils.safestring import mark_safe
from django.utils.translation import gettext_lazy as _

from . import widgets
from .exceptions import FieldError
from .fields import Field
from .instance_loaders import ModelInstanceLoader
from .results import Error, Result, RowResult
from .utils import atomic_if_using_transaction

logger = logging.getLogger(__name__)
# Set default logging handler to avoid "No handler found" warnings.
logger.addHandler(logging.NullHandler())


def get_related_model(field):
    if hasattr(field, "related_model"):
        return field.related_model


def has_natural_foreign_key(model):
    """
    Determine if a model has natural foreign key functions
    """
    return hasattr(model, "natural_key") and hasattr(
        model.objects, "get_by_natural_key"
    )


class ResourceOptions:
    """
    The inner Meta class allows for class-level configuration of how the
    Resource should behave. The following options are available:
    """

    model = None
    """
    Django Model class or full application label string. It is used to introspect
    available fields.

    """
    fields = None
    """
    Controls what introspected fields the Resource should include. A whitelist
    of fields.
    """

    exclude = None
    """
    Controls what introspected fields the Resource should
    NOT include. A blacklist of fields.
    """

    instance_loader_class = None
    """
    Controls which class instance will take
    care of loading existing objects.
    """

    import_id_fields = ["id"]
    """
    Controls which object fields will be used to
    identify existing instances.
    """

    import_order = None
    """
    Controls import order for columns.
    """

    export_order = None
    """
    Controls export order for columns.
    """

    widgets = None
    """
    This dictionary defines widget kwargs for fields.
    """

    use_transactions = None
    """
    Controls if import should use database transactions. Default value is
    ``None`` meaning ``settings.IMPORT_EXPORT_USE_TRANSACTIONS`` will be
    evaluated.
    """

    skip_unchanged = False
    """
    Controls if the import should skip unchanged records.
    If ``True``, then each existing instance is compared with the instance to be
    imported, and if there are no changes detected, the row is recorded as skipped,
    and no database update takes place.

    The advantages of enabling this option are:

    #. Avoids unnecessary database operations which can result in performance
       improvements for large datasets.

    #. Skipped records are recorded in each :class:`~import_export.results.RowResult`.

    #. Skipped records are clearly visible in the
       :ref:`import confirmation page<import-process>`.

    For the default ``skip_unchanged`` logic to work, the
    :attr:`~import_export.resources.ResourceOptions.skip_diff` must also be ``False``
    (which is the default):

    Default value is ``False``.
    """

    report_skipped = True
    """
    Controls if the result reports skipped rows. Default value is ``True``.
    """

    clean_model_instances = False
    """
    Controls whether ``instance.full_clean()`` is called during the import
    process to identify potential validation errors.
    """

    def __init__(self, **kwargs):
        """
        Initializes the ResourceOptions instance with the provided keyword
        arguments.
        """
        self._meta = kwargs

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
        Returns a queryset of all objects for this model. Override this if you
        want to limit the returned queryset.
        """
        return self._meta.model.objects.all()

    def init_instance(self, row=None):
        """
        Initializes a new Django model.
        """
        return self._meta.model()

    def after_import(self, dataset, result, **kwargs):
        """
        Resets the SQL sequences after new objects are imported
        """
        # Adapted from django's loaddata
        dry_run = self._is_dry_run(kwargs)
        if not dry_run and any(
            r.import_type == RowResult.IMPORT_TYPE_NEW for r in result.rows
        ):
            db_connection = self.get_db_connection_name()
            connection = connections[db_connection]
            sequence_sql = connection.ops.sequence_reset_sql(
                no_style(), [self._meta.model]
            )
            if sequence_sql:
                cursor = connection.cursor()
                try:
                    for line in sequence_sql:
                        cursor.execute(line)
                finally:
                    cursor.close()

    def _is_dry_run(self, kwargs):
        """
        Determines if the import is a dry run based on the provided keyword
        arguments.
        """
        return kwargs.get("dry_run", False)

    @classmethod
    def get_display_name(cls):
        if hasattr(cls._meta, "name"):
            return cls._meta.name
        return cls.__name__

    @classmethod
    def widget_from_django_field(cls, django_field):
        """
        Returns the widget that would likely be associated with each
        Django type.

        Includes mapping of Postgres Array field. In the case that
        psycopg2 is not installed, we consume the error and process the field
        regardless.
        """
        result = widgets.Widget
        internal_type = ""
        if callable(getattr(django_field, "get_internal_type", None)):
            internal_type = django_field.get_internal_type()

        if internal_type in cls.WIDGETS_MAP:
            result = cls.WIDGETS_MAP[internal_type]
            if isinstance(result, str):
                result = getattr(cls, result)(django_field)
        else:
            try:
                from django.contrib.postgres.fields import ArrayField
            except ImportError:
                # ImportError: No module named psycopg2.extras
                class ArrayField:
                    pass

            if isinstance(django_field, ArrayField):
                return widgets.SimpleArrayWidget

        return result

    @classmethod
    def widget_kwargs_for_field(cls, field_name, django_field):
        """
        Returns widget kwargs for given field_name.
        """
        widget_kwargs = {}
        if cls._meta.widgets:
            cls_kwargs = cls._meta.widgets.get(field_name, {})
            widget_kwargs.update(cls_kwargs)
        if (
            issubclass(django_field.__class__, fields.CharField)
            and django_field.blank is True
        ):
            widget_kwargs.update({"coerce_to_string": True, "allow_blank": True})
        return widget_kwargs

    @classmethod
    def field_from_django_field(cls, field_name, django_field, readonly):
        """
        Returns a Resource Field instance for the given Django model field.
        """

        FieldWidget = cls.widget_from_django_field(django_field)
        widget_kwargs = cls.widget_kwargs_for_field(field_name, django_field)
        field = cls.DEFAULT_RESOURCE_FIELD(
            attribute=field_name,
            column_name=field_name,
            widget=FieldWidget(**widget_kwargs),
            readonly=readonly,
            default=django_field.default,
        )
        return field

    def get_queryset(self):
        """
