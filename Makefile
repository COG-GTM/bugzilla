# Parity harness: the same black-box REST tests run against the Perl app and
# the Python port.
#
#   make parity TARGET=perl      (default)
#   make parity TARGET=python
#
# Set BASE_URL to override the URL for either target.

TARGET ?= perl
PERL_BASE_URL ?= http://127.0.0.1:8080/rest.cgi
PYTHON_BASE_URL ?= http://127.0.0.1:8000/rest.cgi
PYTHON ?= python3
PYTEST_ARGS ?=

ifeq ($(TARGET),perl)
BASE_URL ?= $(PERL_BASE_URL)
else ifeq ($(TARGET),python)
BASE_URL ?= $(PYTHON_BASE_URL)
else
$(error TARGET must be perl or python, got "$(TARGET)")
endif

.PHONY: parity parity-report parity-deps seed

parity:
	@echo "parity: TARGET=$(TARGET) BASE_URL=$(BASE_URL)"
	BASE_URL=$(BASE_URL) PARITY_TARGET=$(TARGET) $(PYTHON) -m pytest parity $(PYTEST_ARGS)

# endpoint | Perl | Python table, from one run against each target.
parity-report:
	$(PYTHON) parity/report.py

parity-deps:
	$(PYTHON) -m pip install -r parity/requirements.txt

seed:
	BASE_URL=$(BASE_URL) $(PYTHON) scripts/seed_demo_data.py
