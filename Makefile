# Parity harness for the Perl -> Python migration (see docs/migration/PLAN.md).
#
#   make parity                 # against Perl (default)
#   make parity TARGET=python   # against the FastAPI service
#   make parity BASE_URL=...    # against any REST base URL
#   make seed                   # load the demo data the suite expects

TARGET ?= perl
PERL_BASE_URL ?= http://127.0.0.1:8080/rest.cgi
PYTHON_BASE_URL ?= http://127.0.0.1:8000/rest.cgi
PYTEST_ARGS ?=
PYTHON ?= python3

ifeq ($(TARGET),perl)
BASE_URL ?= $(PERL_BASE_URL)
else ifeq ($(TARGET),python)
BASE_URL ?= $(PYTHON_BASE_URL)
else ifeq ($(TARGET),proxied)
# Apache front door: Perl plus whatever routes are proxied to Python.
BASE_URL ?= $(PERL_BASE_URL)
else
$(error TARGET must be perl, python or proxied, got '$(TARGET)')
endif

.PHONY: parity parity-deps parity-report seed

parity-deps:
	$(PYTHON) -m pip install -q -r parity/requirements.txt

# URLs reach the recipes through the environment, never through shell text.
export BASE_URL PERL_BASE_URL PYTHON_BASE_URL

seed:
	$(PYTHON) scripts/seed_demo_data.py --base-url "$$PERL_BASE_URL"

parity:
	$(info parity: TARGET=$(TARGET) BASE_URL=$(BASE_URL))
	$(PYTHON) -m pytest parity -p no:cacheprovider -q $(PYTEST_ARGS)

# endpoint | Perl | Python table (runs the suite against both targets)
parity-report:
	$(PYTHON) parity/report.py --perl "$$PERL_BASE_URL" --python "$$PYTHON_BASE_URL" $(if $(REPORT_OUT),--markdown $(REPORT_OUT),)
