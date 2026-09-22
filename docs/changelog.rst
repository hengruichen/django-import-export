Changelog
=========

Please refer to :doc:`release notes<release_notes>`.

4.0.0-beta.2 (unreleased)
--------------------------

- fix declaring existing model field(s) in ModelResource altering export order (#1663)
- Updated `docker-compose` command with latest version syntax in `runtests.sh` (#1686)
- Support export from model change form (#1687)
- Updated Admin UI to track deleted and skipped Imports (#1691)
- Import form defaults to read-only field if only one format defined (#1690)
- Refactored :module:`~import_export.resources` into separate modules for ``declarative`` and ``options`` (#1695)
- fix multiple inheritance not setting options (#1696)
- Refactored tests to remove dependencies between tests (#1703)

4.0.0-beta.1 (2023-11-16)
--------------------------

Deprecations
############

- Removed v3 deprecations (#1629)
- Deprecation of ``ExportViewFormMixin`` (#1666)

Enhancements
############

- Refactor ordering logic (#1626)

  - Refactor 'diff' logic to avoid calling dehydrate methods

  - Refactor declarations of ``fields``, ``import_order`` and ``export_order`` to fix ordering issues

- refactor to export HTML / formulae escaping updates (#1638)
- removed unused variable ``Result.new_record`` (#1640)
- Refactor ``resources.py`` to standardise method args (#1641)
- added specific check for missing ``import_id_fields`` (#1645)
- Enable optional tablib dependencies (#1647)
- added :meth:`~import_export.widgets.ForeignKeyWidget.get_lookup_kwargs` to make it easier to override object
  lookup (#1651)
- Standardised interface of :meth:`~import_export.widgets.Widget.render` (#1657)
- Added :meth:`~import_export.resources.Resource.do_instance_save` helper method (#1668)
- Enable defining Resource model as a string (#1669)
- Support multiple Resources for export (#1671)

Fixes
#####

- dynamic widget parameters for CharField fixes 'NOT NULL constraint' error in xlsx (#1485)
- fix cooperation with adminsortable2 (#1633)
- Removed unused method ``utils.original()``
- Fix deprecated ``log_action`` method (#1673)

Development
###########

- Refactor build process (#1630)
- Refactored ``test_admin_integration()``: split into smaller test modules (#1662)
- Refactored ``test_resources()``: split into smaller test modules (#1672)

Documentation
#############

- Clarified ``skip_diff`` documentation (#1655)
- Improved documentation relating to validation on import (#1665)

3.3.4 (unreleased)
------------------

- Added `CONTRIBUTING.md`
- Show list of exported fields in Admin UI (#1685)

3.3.3 (2023-11-11)
------------------

- :meth:`~import_export.admin.ExportActionMixin.export_admin_action` can be overridden by subclassing it in the
  ``ModelAdmin`` (#1681)

3.3.2 (2023-11-09)
------------------

- Updated Spanish translations (#1639)
- Added documentation and tests for retrieving instance information after import (#1643)
- :meth:`~import_export.widgets.NumberWidget.render` returns ``None`` as empty string
  if ``coerce_to_string`` is True (#1650)
- Updated documentation to describe how to select for export in Admin UI (#1670)
- Added catch for django5 deprecation warning (#1676)
- Updated and compiled message files (#1678)

3.3.1 (2023-09-14)
------------------

- Added `.readthedocs.yaml` (#1625)

3.3.0 (2023-09-14)
------------------

Deprecations
############

- Remove 'escape output' deprecation (#1618)

  - Removal of deprecated :ref:`IMPORT_EXPORT_ESCAPE_OUTPUT_ON_EXPORT`.

  - Deprecation of :ref:`IMPORT_EXPORT_ESCAPE_HTML_ON_EXPORT`.  Refer to :ref:`installation` docs.

Enhancements
############

- Refactoring and fix to support filtering exports (#1579)
- Store ``instance`` and ``original`` object in :class:`~import_export.results.RowResult` (#1584)
- Add customizable blocks in import.html (#1598)
- Include 'allowed formats' settings (#1606)
- Add kwargs to enable CharWidget to return values as strings (#1623)

Internationalization
####################

- Add Finnish translation (#1588)
- Updated ru translation (#16
# ... [truncated] ...
if import throws exception (#377)

- Fixes error when a single value is stored in m2m relation field (#177)

- Add support for django.db.models.TimeField (#381)


0.4.2 (2015-12-18)
------------------

- add xlsx import support


0.4.1 (2015-12-11)
------------------

- fix for fields with a dyanmic default callable (#360)


0.4.0 (2015-12-02)
------------------

- Add Django 1.9 support

- Django 1.4 is not supported (#348)


0.3.1 (2015-11-20)
------------------

- FIX: importing csv in python 3


0.3 (2015-11-20)
----------------

- FIX: importing csv UnicodeEncodeError introduced in 0.2.9 (#347)


0.2.9 (2015-11-12)
------------------

- Allow Field.save() relation following (#344)

- Support default values on fields (and models) (#345)

- m2m widget: allow trailing comma (#343)

- Open csv files as text and not binary (#127)


0.2.8 (2015-07-29)
------------------

- use the IntegerWidget for database-fields of type BigIntegerField (#302)

- make datetime timezone aware if USE_TZ is True (#283).

- Fix 0 is interpreted as None in number widgets (#274)

- add possibility to override tmp storage class (#133, #251)

- better error reporting (#259)


0.2.7 (2015-05-04)
------------------

- Django 1.8 compatibility

- add attribute inheritance to Resource (#140)

- make the filename and user available to import_data (#237)

- Add to_encoding functionality (#244)

- Call before_import before creating the instance_loader - fixes #193


0.2.6 (2014-10-09)
------------------

- added use of get_diff_headers method into import.html template (#158)

- Try to use OrderedDict instead of SortedDict, which is deprecated in
  Django 1.7 (#157)

- fixed #105 unicode import

- remove invalid form action "form_url" #154


0.2.5 (2014-10-04)
------------------

- Do not convert numeric types to string (#149)

- implement export as an admin action (#124)


0.2.4 (2014-09-18)
------------------

- fix: get_value raised attribute error on model method call

- Fixed XLS import on python 3. Optimized loop

- Fixed properly skipping row marked as skipped when importing data from
  the admin interface.

- Allow Resource.export to accept iterables as well as querysets

- Improve error messages

- FIX: Properly handle NullBoleanField (#115) - Backward Incompatible Change
  previously None values were handled as false


0.2.3 (2014-07-01)
------------------

- Add separator and field keyword arguments to ManyToManyWidget

- FIX: No support for dates before 1900 (#93)


0.2.2 (2014-04-18)
------------------

- RowResult now stores exception object rather than it's repr

- Admin integration - add EntryLog object for each added/updated/deleted instance


0.2.1 (2014-02-20)
------------------

- FIX import_file_name form field can be use to access the filesystem (#65)


0.2.0 (2014-01-30)
------------------

- Python 3 support


0.1.6 (2014-01-21)
------------------

* Additional hooks for customizing the workflow (#61)

0.1.5 (2013-11-29)
------------------

* Prevent queryset caching when exporting (#44)

* Allow unchanged rows to be skipped when importing (#30)

* Update tests for Django 1.6 (#57)

* Allow different ``ResourceClass`` to be used in ``ImportExportModelAdmin``
  (#49)

0.1.4
-----

* Use ``field_name`` instead of ``column_name`` for field dehydration, FIX #36

* Handle OneToOneField,  FIX #17 - Exception when attempting access something
  on the related_name.

* FIX #23 - export filter not working

0.1.3
-----

* Fix packaging

* DB transactions support for importing data

0.1.2
-----

* support for deleting objects during import

* bug fixes

* Allowing a field to be 'dehydrated' with a custom method

* added documentation

0.1.1
-----

* added ExportForm to admin integration for choosing export file format

* refactor admin integration to allow better handling of specific formats
  supported features and better handling of reading text files

* include all available formats in Admin integration

0.1.0
-----

* Refactor api

