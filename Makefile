# Bugzilla Perl -> Python migration helpers.
#
#   make parity                # run the REST contract suite against the Perl app (default)
#   make parity TARGET=python  # run the same suite against the Python port
#   make parity-update-golden  # (re)record the contract from the Perl app
#   make seed                  # load the demo dataset through the REST API

TARGET ?= perl
PERL_BASE_URL   ?= http://127.0.0.1:8080/rest.cgi
PYTHON_BASE_URL ?= http://127.0.0.1:8000/rest.cgi
PYTHON ?= python3
PYTEST_ARGS ?=

ifeq ($(TARGET),perl)
  BASE_URL := $(PERL_BASE_URL)
else ifeq ($(TARGET),python)
  BASE_URL := $(PYTHON_BASE_URL)
else
  $(error TARGET must be "perl" or "python", got "$(TARGET)")
endif

.PHONY: parity parity-deps parity-update-golden seed

parity-deps:
	$(PYTHON) -m pip install -q -r parity/requirements.txt

parity:
	@echo "parity: TARGET=$(TARGET) BASE_URL=$(BASE_URL)"
	cd parity && BASE_URL=$(BASE_URL) $(PYTHON) -m pytest $(PYTEST_ARGS)

parity-update-golden:
	@test "$(TARGET)" = perl || { echo "golden files may only be recorded from the Perl app (TARGET=perl)"; exit 1; }
	cd parity && BASE_URL=$(BASE_URL) $(PYTHON) -m pytest --update-golden -q $(PYTEST_ARGS)

seed:
	$(PYTHON) scripts/seed_demo_data.py --base-url $(BASE_URL)
