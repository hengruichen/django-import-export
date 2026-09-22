# import_export

[![PyPI version](https://badge.fury.io/py/django-import-export.svg)](https://badge.fury.io/py/django-import-export)
[![Documentation Status](https://readthedocs.org/projects/django-import-export/badge/?version=latest)](https://django-import-export.readthedocs.io/en/latest/?badge=latest)
[![Build Status](https://github.com/django-import-export/django-import-export/actions/workflows/tests.yml/badge.svg)](https://github.com/django-import-export/django-import-export/actions/workflows/tests.yml)
[![Coverage Status](https://codecov.io/gh/django-import-export/django-import-export/branch/main/graph/badge.svg?token=821Y00034C)](https://codecov.io/gh/django-import-export/django-import-export)
[![Code style: black](https://img.shields.io/badge/code%20style-black-000000.svg)](https://github.com/psf/black)
[![GitHub license](https://img.shields.io/github/license/django-import-export/django-import-export.svg)](https://github.com/django-import-export/django-import-export/blob/main/LICENSE)

Django application for importing and exporting data.

## Documentation

The documentation is available at [django-import-export.readthedocs.io](https://django-import-export.readthedocs.io/).

## Installation

Install the package using pip:

```bash
pip install django-import-export
```

Add `'import_export'` to `INSTALLED_APPS` in your `settings.py`:

```python
INSTALLED_APPS = [
    # ...
    "import_export",
]
```

## Importing

The `ImportExportMixin` can be used to add import/export functionality to a `ModelAdmin`:

```python
from django.contrib import admin
from import_export import resources
from import_export.admin import ImportExportModelAdmin

from .models import MyModel


class MyModelResource(resources.ModelResource):
    class Meta:
        model = MyModel


@admin.register(MyModel)
class MyModelAdmin(ImportExportModelAdmin):
    resource_class = MyModelResource
```

The `ImportExportModelAdmin` adds a "Import" button to the admin change list. Clicking on it will open an import form. The form can be configured using the `import_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ImportExportModelAdmin):
    import_form_class = MyModelImportForm
```

The `import_form_class` must be a subclass of `ImportForm`. It can be used to customize the import form, for example to add additional fields or to validate the uploaded file.

The `ImportExportModelAdmin` adds a "Export" button to the admin change list. Clicking on it will open an export form. The form can be configured using the `export_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ImportExportModelAdmin):
    export_form_class = MyModelExportForm
```

The `export_form_class` must be a subclass of `ExportForm`. It can be used to customize the export form, for example to add additional fields or to validate the selected rows.

The `ImportExportModelAdmin` adds an "Export selected" action to the admin change list. Clicking on it will export the selected rows. The action can be configured using the `export_action_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ImportExportModelAdmin):
    export_action_form_class = MyModelExportActionForm
```

The `export_action_form_class` must be a subclass of `ExportActionForm`. It can be used to customize the export action, for example to add additional fields or to validate the selected rows.

The `ImportExportModelAdmin` adds a "Import selected" action to the admin change list. Clicking on it will import the selected rows. The action can be configured using the `import_action_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ImportExportModelAdmin):
    import_action_form_class = MyModelImportActionForm
```

The `import_action_form_class` must be a subclass of `ImportActionForm`. It can be used to customize the import action, for example to add additional fields or to validate the uploaded file.

The `ImportExportModelAdmin` adds a "Import" button to the admin change list. Clicking on it will open an import form. The form can be configured using the `import_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ImportExportModelAdmin):
    import_form_class = MyModelImportForm
```

The `import_form_class` must be a subclass of `ImportForm`. It can be used to customize the import form, for example to add additional fields or to validate the uploaded file.

The `ImportExportModelAdmin` adds a "Export" button to the admin change list. Clicking on it will open an export form. The form can be configured using the `export_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ImportExportModelAdmin):
    export_form_class = MyModelExportForm
```

The `export_form_class` must be a subclass of `ExportForm`. It can be used to customize the export form, for example to add additional fields or to validate the selected rows.

The `ImportExportModelAdmin` adds an "Export selected" action to the admin change list. Clicking on it will export the selected rows. The action can be configured using the `export_action_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ImportExportModelAdmin):
    export_action_form_class = MyModelExportActionForm
```

The `export_action_form_class` must be a subclass of `ExportActionForm`. It can be used to customize the export action, for example to add additional fields or to validate the selected rows.

The `ImportExportModelAdmin` adds a "Import selected" action to the admin change list. Clicking on it will import the selected rows. The action can be configured using the `import_action_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ImportExportModelAdmin):
    import_action_form_class = MyModelImportActionForm
```

The `import_action_form_class` must be a subclass of `ImportActionForm`. It can be used to customize the import action, for example to add additional fields or to validate the uploaded file.

## Exporting

The `ExportMixin` can be used to add import/export functionality to a `ModelAdmin`:

```python
from django.contrib import admin
from import_export import resources
from import_export.admin import ExportActionModelAdmin

from .models import MyModel


class MyModelResource(resources.ModelResource):
    class Meta:
        model = MyModel


@admin.register(MyModel)
class MyModelAdmin(ExportActionModelAdmin):
    resource_class = MyModelResource
```

The `ExportActionModelAdmin` adds an "Export" button to the admin change list. Clicking on it will open an export form. The form can be configured using the `export_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ExportActionModelAdmin):
    export_form_class = MyModelExportForm
```

The `export_form_class` must be a subclass of `ExportForm`. It can be used to customize the export form, for example to add additional fields or to validate the selected rows.

The `ExportActionModelAdmin` adds a "Export selected" action to the admin change list. Clicking on it will export the selected rows. The action can be configured using the `export_action_form_class` attribute:

```python
@admin.register(MyModel)
class MyModelAdmin(ExportActionModelAdmin):
    export_action_form_class = MyModelExportActionForm
```

The `export_action_form_class` must be a subclass of `ExportActionForm`. It can be used to customize the export action, for example to add additional fields or to validate the selected rows.

## Contributing

Contributions are welcome! Please read the [contributing guide](https://django-import-export.readthedocs.io/en/latest/contributing.html) for more information.

## License

This project is licensed under the [MIT License](https://github.com/django-import-export/django-import-export/blob/main/LICENSE).
