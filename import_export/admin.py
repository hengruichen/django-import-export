import logging
import warnings

import django
from django import forms
from django.conf import settings
from django.contrib import admin, messages
from django.contrib.admin.models import ADDITION, CHANGE, DELETION, LogEntry
from django.contrib.auth import get_permission_codename
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import PermissionDenied
from django.http import HttpResponse, HttpResponseRedirect
from django.template.response import TemplateResponse
from django.urls import path, reverse
from django.utils.decorators import method_decorator
from django.utils.module_loading import import_string
from django.utils.translation import gettext_lazy as _
from django.views.decorators.http import require_POST

from .forms import (
    ConfirmImportForm,
    ExportForm,
    ImportExportFormBase,
    ImportForm,
    export_action_form_factory,
)
from .mixins import BaseExportMixin, BaseImportMixin
from .results import RowResult
from .signals import post_export, post_import
from .tmp_storages import TempFolderStorage
from .utils import original

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
            self.base_change_list_template = self.change_list_template
        else:
            self.base_change_list_template = "admin/change_list.html"

        try:
            self.change_list_template = getattr(
                self, "import_export_change_list_template", None
            )
        except AttributeError:
            logger.warning("failed to assign change_list_template attribute")

        if self.change_list_template is None:
            self.change_list_template = self.base_change_list_template

    def get_model_info(self):
        app_label = self.model._meta.app_label
        return (app_label, self.model._meta.model_name)

    def changelist_view(self, request, extra_context=None):
        extra_context = extra_context or {}
        extra_context["base_change_list_template"] = self.base_change_list_template
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

        if isinstance(tmp_storage_class, str):
            tmp_storage_class = import_string(tmp_storage_class)

        return tmp_storage_class

    def get_tmp_storage(self, request):
        """
        Returns a new instance of the configured temporary file storage.
        """
        return self.get_tmp_storage_class()(request)

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    def get_import_form(self, request, file_format):
        """
        Returns the import form class to use.
        """
        if file_format == "csv":
            return self.import_form_class
        else:
            return self.confirm_form_class

    def get_import_form_kwargs(self, request, file_format, queryset):
        """
        Returns the keyword arguments for instantiating the import form.
        """
        kwargs = {
            "data": request.POST,
            "files": request.FILES,
            "user": request.user,
            "queryset": queryset,
            "model": self.model,
            "opts": self.model._meta,
            "format": file_format,
            "tmp_storage": self.get_tmp_storage(request),
        }

        return kwargs

    def get_import_data(self, request, queryset, file_format):
        """
        Returns the import data.
        """
        return self.get_import_form(request, file_format)(
            self.get_import_form_kwargs(request, file_format, queryset)
        ).cleaned_data["data"]

    def get_import_resource_class(self, file_format):
        """
        Returns the import resource class to use.
        """
        return self.get_import_formats()[file_format]

    def get_import_resource_kwargs(self, request, file_format, queryset):
        """
        Returns the keyword arguments for instantiating the import resource.
        """
        kwargs = {
            "import_formats": self.get_import_formats(),
            "user": request.user,
            "queryset": queryset,
            "model": self.model,
            "opts": self.model._meta,
            "format": file_format,
            "tmp_storage": self.get_tmp_storage(request),
        }

        return kwargs

    def get_import_data_resource(self, request, queryset, file_format):
        """
        Returns the import data resource.
        """
        return self.get_import_resource_class(file_format)(
            self.get_import_resource_kwargs(request, file_format, queryset)
        )

    def get_import_data_resource_kwargs(self, request, queryset, file_format):
        """
        Returns the keyword arguments for instantiating the import data
        resource.
        """
        kwargs = {
            "data": self.get_import_data(request, queryset, file_format),
            "user": request.user,
            "queryset": queryset,
            "model": self.model,
            "opts": self.model._meta,
            "format": file_format,
            "tmp_storage": self.get_tmp_storage(request),
        }

        return kwargs

    def get_import_results(self, request, queryset, file_format):
        """
        Returns the import results.
        """
        return self.get_import_data_resource_kwargs(
            request, queryset, file_format
        )["import_data"]()

    def get_import_results_class(self, file_format):
        """
        Returns the import results class to use.
        """
        return self.get_import_formats()[file_format]

    def get_import_results_kwargs(self, request, queryset, file_format):
        """
        Returns the keyword arguments for instantiating the import results.
        """
        kwargs = {
            "data": self.get_import_data(request, queryset, file_format),
            "user": request.user,
            "queryset": queryset,
            "model": self.model,
            "opts": self.model._meta,
            "format": file_format,
            "tmp_storage": self.get_tmp_storage(request),
        }

        return kwargs

    def get_import_results_resource(self, request, queryset, file_format):
        """
        Returns the import results resource.
        """
        return self.get_import_results_class(file_format)(
            self.get_import_results_kwargs(request, queryset, file_format)
        )

    def get_import_results_resource_kwargs(self, request, queryset, file_format):
        """
        Returns the keyword arguments for instantiating the import results
        resource.
        """
        kwargs = {
            "data": self.get_import_results(request, queryset, file_format),
            "user": request.user,
            "queryset": queryset,
            "model": self.model,
            "opts": self.model._meta,
            "format": file_format,
            "tmp_storage": self.get_tmp_storage(request),
        }

        return kwargs

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        form.save()

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                CHANGE,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural},
            )

        opts = self.model._meta
        if self.get_skip_admin_log():
            message = _("Imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }
        else:
            message = _("Successfully imported %(count)d %(items)s.") % {
                "count": form.cleaned_data["total"],
                "items": opts.verbose_name_plural,
            }

        if form.cleaned_data["update"]:
            message += _(" %(changed)d were changed and %(deleted)d were deleted.") % {
                "changed": form.cleaned_data["changed"],
                "deleted": form.cleaned_data["deleted"],
            }

        messages.success(request, message)

        post_import.send(sender=self.__class__, request=request, form=form)

        return HttpResponseRedirect(
            reverse(
                "admin:%s_%s_changelist"
                % (opts.app_label, opts.model_name),
            )
        )

    @method_decorator(require_POST)
    def import_action(self, request, form, file_format, queryset):
        """
        Handles the import process.
        """
        if not self.has_import_permission(request):
            raise PermissionDenied

        if not self.get_skip_admin_log():
            self.log_action(
                request,
                ADDITION,
                None,
                _("Imported %(count)d %(items)s."),
                {"count": form.cleaned_data["total"], "items": self.opts.verbose_name_plural