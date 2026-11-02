# Development entry points. Run `make help` to list them.
# Writing to the robot is deliberately absent: use the stackchan-flash skill.

SHELL := /bin/bash
.DEFAULT_GOAL := help

BOARD := m5stack/core-s3
BOARD_VARIANT := m5stack-core-s3
LANGUAGE := es-ES
SERVER_DIR := server/main/xiaozhi-server
SERVER_PY := $(CURDIR)/$(SERVER_DIR)/.venv/bin/python
IDF_ENV := source $(CURDIR)/tools/idf-env.sh >/dev/null

.PHONY: help check-system setup setup-submodules setup-server setup-firmware \
	test test-server test-firmware test-tools \
	firmware firmware-image firmware-index server-config server-run

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

check-system: ## Report missing system packages
	@tools/check-system.sh

setup: check-system setup-submodules setup-server setup-firmware ## Install everything needed to develop and test

setup-submodules: ## Fetch firmware and server at their pinned commits
	git submodule update --init

setup-server: ## Create the server's Python 3.10 environment (CPU torch)
	cd $(SERVER_DIR) && uv venv --allow-existing --python 3.10 .venv
	cd $(SERVER_DIR) && uv pip install --python .venv/bin/python --torch-backend=cpu \
		-r requirements.txt -r requirements-dev.txt
	uv pip check --python $(SERVER_PY)

setup-firmware: ## Install ESP-IDF v6.1 and its Python 3.12 environment
	@tools/setup-idf.sh

test: test-tools test-server test-firmware ## Run every test suite

test-server: ## Run the server tests
	cd $(SERVER_DIR) && $(SERVER_PY) -m pytest -q

test-firmware: ## Run the firmware's host-side script tests
	$(IDF_ENV) && cd firmware && python -m unittest discover -s scripts/tests
	$(IDF_ENV) && cd firmware/docker/firmware-builder && python -m unittest test_firmware_builder

test-tools: ## Run this repository's tool and skill tests
	python3 -m unittest discover -s tools/tests
	cd .claude/skills/stackchan-flash/scripts && python3 -m unittest test_stackchan_flash
	@if [ -f .claude/skills/local-model/scripts/test_model_gate.py ]; then \
		cd .claude/skills/local-model/scripts && python3 -m unittest test_model_gate; \
	fi

firmware: ## Build the firmware for the configured board
	$(IDF_ENV) && cd firmware && python scripts/build.py $(BOARD) --name $(BOARD_VARIANT)

firmware-image: ## Build an image in dist/firmware; set PURPOSE, optional LANGUAGE, XIAOZHI_CLOUD=1
	$(IDF_ENV) && python $(CURDIR)/tools/build_firmware_image.py --purpose "$(PURPOSE)" \
		--language "$(LANGUAGE)" $(if $(XIAOZHI_CLOUD),--xiaozhi-cloud)

firmware-index: ## Refresh the compile database that VS Code reads
	$(IDF_ENV) && cd firmware && idf.py reconfigure

server-config: ## Generate the gitignored server config for local services
	python3 tools/gen_server_config.py $(if $(FORCE),--force)

server-run: ## Start the server; the local LLM and voice services run separately
	cd $(SERVER_DIR) && $(SERVER_PY) app.py
