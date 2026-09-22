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
    process to identify potential errors. Default value is ``False``.
    """

    def __init__(self, **options):
        for key, value in options.items():
            setattr(self, key, value)

    def __repr__(self):
        return "<ResourceOptions>"


class ModelResourceOptions(ResourceOptions):
    """
    The inner Meta class allows for class-level configuration of how the
    ModelResource should behave. The following options are available:
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
    process to identify potential errors. Default value is ``False``.
    """

    def __init__(self, **options):
        for key, value in options.items():
            setattr(self, key, value)

    def __repr__(self):
        return "<ModelResourceOptions>"


class ModelDeclarativeMetaclass(type):
    """
    Metaclass for ModelResource. It allows to define fields declaratively.
    """

    def __new__(cls, name, bases, attrs):
        if "Meta" in attrs:
            meta = attrs["Meta"]
            if hasattr(meta, "model"):
                attrs["model"] = apps.get_model(meta.model)
            elif hasattr(meta, "model_name"):
                attrs["model"] = apps.get_model(meta.model_name)
            if hasattr(meta, "import_id_fields"):
                attrs["import_id_fields"] = meta.import_id_fields
            if hasattr(meta, "fields"):
                attrs["fields"] = meta.fields
            if hasattr(meta, "exclude"):
                attrs["exclude"] = meta.exclude
            if hasattr(meta, "import_order"):
                attrs["import_order"] = meta.import_order
            if hasattr(meta, "export_order"):
                attrs["export_order"] = meta.export_order
            if hasattr(meta, "widgets"):
                attrs["widgets"] = meta.widgets
            if hasattr(meta, "use_transactions"):
                attrs["use_transactions"] = meta.use_transactions
            if hasattr(meta, "skip_unchanged"):
                attrs["skip_unchanged"] = meta.skip_unchanged
            if hasattr(meta, "report_skipped"):
                attrs["report_skipped"] = meta.report_skipped
            if hasattr(meta, "clean_model_instances"):
                attrs["clean_model_instances"] = meta.clean_model_instances
        return type.__new__(cls, name, bases, attrs)


class Resource:
    """
    The Resource class is used to define the import/export behavior for a
    specific Django model. It is instantiated with a model class, and
    optionally with a list of fields to include/exclude.
    """

    DEFAULT_RESOURCE_FIELD = Field
    DEFAULT_RESOURCE_IMPORT_FIELD = Field
    DEFAULT_RESOURCE_EXPORT_FIELD = Field
    DEFAULT_RESOURCE_IMPORT_RESOURCE = ModelResource
    DEFAULT_RESOURCE_EXPORT_RESOURCE = ModelResource
    DEFAULT_RESOURCE_INSTANCE_LOADER = ModelInstanceLoader
    DEFAULT_RESOURCE_INSTANCE_READER = ModelInstanceReader
    DEFAULT_RESOURCE_INSTANCE_WRITER = ModelInstanceWriter
    DEFAULT_RESOURCE_IMPORTER = ModelImporter
    DEFAULT_RESOURCE_EXPORTER = ModelExporter
    DEFAULT_RESOURCE_IMPORTER_WRITER = ModelImporterWriter
    DEFAULT_RESOURCE_EXPORTER_READER = ModelExporterReader
    DEFAULT_RESOURCE_IMPORTER_READER = ModelImporterReader
    DEFAULT_RESOURCE_EXPORTER_WRITER = ModelExporterWriter
    DEFAULT_RESOURCE_IMPORTER_EXPORTER = ModelImporterExporter
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER = ModelImporterExporterReader
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_WRITER = ModelImporterExporterWriter
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER = (
        ModelImporterExporterReaderWriter
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER = (
        ModelImporterExporterReaderWriterReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER = (
        ModelImporterExporterReaderWriterReaderWriter
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriter
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriter
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER = (
        ModelImporterExporterReaderWriterReaderWriterReaderWriterReaderWriterReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReaderReader
    )
    DEFAULT_RESOURCE_IMPORTER_EXPORTER_READER_WRITER_READER_WRITER_READER_WRITER_READER_WRITER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_READER_RE