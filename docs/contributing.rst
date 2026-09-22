Contributing
============

Contributions are welcome! Please read this document before submitting a pull request.

Development Setup
-----------------

1. Fork the repository on GitHub.
2. Clone your fork locally::

    git clone https://github.com/YOUR_USERNAME/django-import-export.git
    cd django-import-export

3. Install the development dependencies::

    pip install -r requirements-dev.txt

4. Create a virtual environment and activate it::

    python -m venv venv
    source venv/bin/activate  # On Windows: venv\Scripts\activate

5. Install the package in editable mode with the development dependencies::

    pip install -e .[dev]

6. Install pre-commit hooks::

    pre-commit install

Testing
-------

1. Run the tests::

    pytest

2. Run the tests with coverage::

    pytest --cov=import_export

Documentation
-------------

1. Run the documentation locally::

    cd docs
    make html

2. Open ``docs/_build/html/index.html`` in your browser to view the documentation.

Submitting Changes
------------------

1. Create a new branch for your changes::

    git checkout -b my-feature-branch

2. Make your changes and commit them with a descriptive message::

    git commit -m "Add new feature"

3. Push your branch to your fork on GitHub::

    git push origin my-feature-branch

4. Submit a pull request to the main repository.

Code Style
----------

This project uses `black <https://github.com/psf/black>`_ for code formatting. All code must be formatted with black before submitting a pull request.

To format the code, run::

    black import_export

Type Checking
-------------

This project uses `mypy <https://github.com/python/mypy>`_ for type checking. All code must pass mypy before submitting a pull request.

To check the types, run::

    mypy import_export

Documentation
-------------

This project uses `Sphinx <https://www.sphinx-doc.org/>`_ for documentation. All documentation must be updated before submitting a pull request.

To build the documentation, run::

    cd docs
    make html

To view the documentation, open ``docs/_build/html/index.html`` in your browser.

Pull Request Checklist
----------------------

- [ ] The code follows the style guidelines of this project.
- [ ] The code is properly documented.
- [ ] The code is properly typed.
- [ ] The code is properly tested.
- [ ] The documentation is properly updated.
