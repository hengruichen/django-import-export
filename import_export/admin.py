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


class ExportActionMixin(ExportMixin):
    """
    Mixin with export functionality implemented as an admin action.
    """

    #: template for change form
    change_form_template = "admin/import_export/change_form.html"

    #: Flag to indicate whether to show 'export' button on change form
    show_change_form_export = True

    # This action will receive a selection of items as a queryset,
    # store them in the context, and then render the 'export' admin form page,
    # so that users can select file format and resource

    def change_view(self, request, object_id, form_url="", extra_context=None):
        extra_context = extra_context or {}
        extra_context["show_change_form_export"] = self.show_change_form_export
        return super().change_view(
            request,
            object_id,
            form_url,
            extra_context=extra_context,
        )

    def response_change(self, request, obj):
        if "_export-item" in request.POST:
            return self.export_admin_action(
                request, self.model.objects.filter(id=obj.id)
            )
        return super().response_change(request, obj)

    def export_admin_action(self, request, queryset):
        """
        Action runs on POST from instance action menu (if enabled).
        """
        formats = self.get_export_formats()
        if (
            getattr(settings, "IMPORT_EXPORT_SKIP_ADMIN_ACTION_EXPORT_UI", False)
            is True
        ):
            file_format = formats[0]()

            export_data = self.get_export_data(
                file_format, queryset, request=request, encoding=self.to_encoding
            )
            content_type = file_format.get_content_type()
            response = HttpResponse(export_data, content_type=content_type)
            response["Content-Disposition"] = 'attachment; filename="%s"' % (
                self.get_export_filename(request, queryset, file_format),
            )
            return response

        form_type = self.get_export_form_class()
        formats = self.get_export_formats()
        form = form_type(
            formats=formats,
            resources=self.get_export_resource_classes(),
            initial={"export_items": list(queryset.values_list("id", flat=True))},
        )
        # selected items are to be stored as a hidden input on the form
        form.fields["export_items"] = MultipleChoiceField(
            widget=MultipleHiddenInput,
            required=False,
            choices=[(str(o), str(o)) for o in queryset.all()],
        )
        context = self.init_request_context_data(request, form)

        # this is necessary to render the FORM action correctly
        # i.e. so the POST goes to the correct URL
        export_url = reverse(
            "%s:%s_%s_export"
            % (
                self.admin_site.name,
                self.model._meta.app_label,
                self.model._meta.model_name,
            )
        )
        context["export_url"] = export_url

        return render(request, "admin/import_export/export.html", context=context)

    def get_actions(self, request):
        """
        Adds the export action to the list of available actions.
        """
        actions = super().get_actions(request)
        actions.update(
            export_admin_action=(
                type(self).export_admin_action,
                "export_admin_action",
                _("Export selected %(verbose_name_plural)s"),
            )
        )
        return actions


class ExportActionModelAdmin(ExportActionMixin, admin.ModelAdmin):
    """
    Subclass of ModelAdmin with export functionality implemented as an
    admin action.
    """


class ImportExportActionModelAdmin(ImportMixin, ExportActionModelAdmin):
    """
    Subclass of ExportActionModelAdmin with import/export functionality.
    Export functionality is implemented as an admin action.
    """

