import logging
import warnings

import django
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth import get_permission_codename
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.forms import MultipleChoiceField, MultipleHiddenInput
from django.http import HttpResponse, HttpResponseRedirect
from django.shortcuts import render
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from .forms import ConfirmImportForm, ExportForm, ImportForm
from .mixins import BaseExportMixin, BaseImportMixin
from .results import RowResult
from .signals import post_export, post_import
from .tmp_storages import TempFolderStorage

logger = logging.getLogger(__name__)


class ImportExportMixinBase:
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self.init_change_list_template()

    def init_change_list_template(self):
        # Store already set change_list_template to allow users to independently
        # customize the change list object tools. This treats the cases where
        # `self.change_list_template` is `None` (the default in `ModelAdmin`) or
        # where `self.import_export_change_list_template` is `None` as falling
        # back on the default templates.
        if getattr(self, "change_list_template", None):
            self.ie_base_change_list_template = self.change_list_template
        else:
            self.ie_base_change_list_template = "admin/change_list.html"

        try:
            self.change_list_template = getattr(
                self, "import_export_change_list_template", None
            )
        except AttributeError:
            logger.warning("failed to assign change_list_template attribute")

        if self.change_list_template is None:
            self.change_list_template = self.ie_base_change_list_template

    def get_model_info(self):
        app_label = self.model._meta.app_label
        return (app_label, self.model._meta.model_name)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context[
            "ie_base_change_list_template"
        ] = self.ie_base_change_list_template
        return super().changelist_view(request, extra_context)


class ImportMixin(BaseImportMixin, ImportExportMixinBase):
    """
    Import mixin.

    This is intended to be mixed with django.contrib.admin.ModelAdmin
    https://docs.djangoproject.com/en/dev/ref/contrib/admin/
    """

    #: template for change_list view
    import_export_change_list_template = "admin/import_export/change_list_import.html"
    #: template for import view
    import_template_name = "admin/import_export/import.html"
    #: form class to use for the initial import step
    import_form_class = ImportForm
    #: form class to use for the confirm import step
    confirm_form_class = ConfirmImportForm
    #: import data encoding
    from_encoding = "utf-8-sig"
    skip_admin_log = None
    # storage class for saving temporary files
    tmp_storage_class = None

    def get_skip_admin_log(self):
        if self.skip_admin_log is None:
            return getattr(settings, "IMPORT_EXPORT_SKIP_ADMIN_LOG", False)
        else:
            return self.skip_admin_log

    def get_tmp_storage_class(self):
        if self.tmp_storage_class is None:
            tmp_storage_class = getattr(
                settings,
                "IMPORT_EXPORT_TMP_STORAGE_CLASS",
                TempFolderStorage,
            )
        else:
            tmp_storage_class = self.tmp_storage_class

        if isinsta
# ... [truncated] ...
Admin):
    """
    Subclass of ModelAdmin with import/export functionality.
    """


class ImportExportMixin(ImportMixin, ExportMixin):
    """
    Import/Export mixin.

    This is intended to be mixed with django.contrib.admin.ModelAdmin
    https://docs.djangoproject.com/en/dev/ref/contrib/admin/
    """

    #: template for change_list view
    import_export_change_list_template = "admin/import_export/change_list_import_export.html"
    #: template for import view
    import_template_name = "admin/import_export/import_export.html"
    #: form class to use for the initial import step
    import_form_class = ImportForm
    #: form class to use for the confirm import step
    confirm_form_class = ConfirmImportForm
    #: import data encoding
    from_encoding = "utf-8-sig"
    skip_admin_log = None
    # storage class for saving temporary files
    tmp_storage_class = None

    def get_skip_admin_log(self):
        if self.skip_admin_log is None:
            return getattr(settings, "IMPORT_EXPORT_SKIP_ADMIN_LOG", False)
        else:
            return self.skip_admin_log

    def get_tmp_storage_class(self):
        if self.tmp_storage_class is None:
            tmp_storage_class = getattr(
                settings,
                "IMPORT_EXPORT_TMP_STORAGE_CLASS",
                TempFolderStorage,
            )
        else:
            tmp_storage_class = self.tmp_storage_class

        if isinsta
# ... [truncated] ...
Admin):
    """
    Subclass of ModelAdmin with import/export functionality.
    """


class ImportExportActionMixin(ImportExportMixinBase, ExportActionMixin):
    """
    Import/Export mixin.

    This is intended to be mixed with django.contrib.admin.ModelAdmin
    https://docs.djangoproject.com/en/dev/ref/contrib/admin/
    """

    #: template for change_list view
    import_export_change_list_template = "admin/import_export/change_list_import_export.html"
    #: template for import view
    import_template_name = "admin/import_export/import_export.html"
    #: form class to use for the initial import step
    import_form_class = ImportForm
    #: form class to use for the confirm import step
    confirm_form_class = ConfirmImportForm
    #: import data encoding
    from_encoding = "utf-8-sig"
    skip_admin_log = None
    # storage class for saving temporary files
    tmp_storage_class = None

    def get_skip_admin_log(self):
        if self.skip_admin_log is None:
            return getattr(settings, "IMPORT_EXPORT_SKIP_ADMIN_LOG", False)
        else:
            return self.skip_admin_log

    def get_tmp_storage_class(self):
        if self.tmp_storage_class is None:
            tmp_storage_class = getattr(
                settings,
                "IMPORT_EXPORT_TMP_STORAGE_CLASS",
                TempFolderStorage,
            )
        else:
            tmp_storage_class = self.tmp_storage_class

        if isinsta
# ... [truncated] ...
Admin):
    """
    Subclass of ExportActionMixin with import/export functionality.
    Export functionality is implemented as an admin action.
    """


class ImportExportActionModelAdmin(ImportExportMixin, ExportActionModelAdmin):
    """
    Subclass of ExportActionModelAdmin with import/export functionality.
    Export functionality is implemented as an admin action.
    """


class ImportExportActionModelAdmin(ImportExportMixin, ExportActionModelAdmin):
    """
    Subclass of ExportActionModelAdmin with import/export functionality.
    Export functionality is implemented as an admin action.
    """


class ImportExportModelAdmin(ImportExportMixin, ImportExportModelAdmin):
    """
    Subclass of ImportExportModelAdmin with import/export functionality.
    Import functionality is implemented as an admin action.
    """

