# Development entry points. Run `make help` to list them.
# Writing to the robot is deliberately absent: use the stackchan-flash skill.

SHELL := /bin/bash
.DEFAULT_GOAL := help

BOARD := elena-stackchan
BOARD_VARIANT := elena-stackchan
LANGUAGE := es-ES
SERVER_DIR := server/main/xiaozhi-server
SERVER_PY := $(CURDIR)/$(SERVER_DIR)/.venv/bin/python
UV_COMPILE := uv pip compile requirements.txt requirements-dev.txt --python-version 3.10 \
	--torch-backend cpu --no-header --quiet
IDF_ENV := source $(CURDIR)/tools/idf-env.sh >/dev/null

.PHONY: help check-system setup setup-submodules setup-server setup-firmware \
	test test-server test-firmware test-tools \
	check-locks check-server-lock check-firmware-lock update-server-lock \
	firmware firmware-image firmware-index server-config server-run screen-viewer

help: ## List targets
	@grep -E '^[a-z-]+:.*## ' $(MAKEFILE_LIST) | awk -F':.*## ' '{printf "  %-18s %s\n", $$1, $$2}'

check-system: ## Report missing system packages
	@tools/check-system.sh

setup: check-system setup-submodules setup-server setup-firmware ## Install everything needed to develop and test

setup-submodules: ## Fetch firmware and server at their pinned commits
	git submodule update --init

setup-server: ## Create the server's Python 3.10 environment (CPU torch)
	cd $(SERVER_DIR) && uv venv --allow-existing --python 3.10 .venv
	cd $(SERVER_DIR) && uv pip sync --python .venv/bin/python --torch-backend=cpu requirements.lock
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
	cd .claude/skills/local-model/scripts && python3 -m unittest test_model_gate
	cd .claude/skills/hermes-delegate/scripts && python3 -m unittest test_delegate

check-locks: check-server-lock check-firmware-lock ## Fail if a lock file no longer matches its inputs

check-server-lock: ## Fail if the server's lock does not match its requirements
	@tmp=$$(mktemp) && cp $(SERVER_DIR)/requirements.lock $$tmp && \
	cd $(SERVER_DIR) && $(UV_COMPILE) -o $$tmp && cmp requirements.lock $$tmp && \
	rm -f $$tmp && echo "Server lock is current."

check-firmware-lock: ## After a build, fail if component versions changed
	git -C firmware diff --exit-code -- dependencies.lock
	@echo "Firmware lock is current."

update-server-lock: ## Refresh the server's lock after editing its requirements
	cd $(SERVER_DIR) && $(UV_COMPILE) -o requirements.lock $(if $(UPGRADE),--upgrade-package $(UPGRADE))

firmware: ## Build the firmware for the configured board
	$(IDF_ENV) && cd firmware && python scripts/build.py $(BOARD) --name $(BOARD_VARIANT) --language $(LANGUAGE)

firmware-image: ## Build an image in dist/firmware; set PURPOSE, optional LANGUAGE, XIAOZHI_CLOUD=1, SCREEN_STREAM=1, AVATAR=<folder>
	$(IDF_ENV) && python $(CURDIR)/tools/build_firmware_image.py --purpose "$(PURPOSE)" \
		--language "$(LANGUAGE)" --board "$(BOARD)" --variant "$(BOARD_VARIANT)" $(if $(XIAOZHI_CLOUD),--xiaozhi-cloud) $(if $(SCREEN_STREAM),--screen-stream) $(if $(AVATAR),--avatar "$(AVATAR)")

firmware-index: ## Refresh the compile database that VS Code reads
	$(IDF_ENV) && cd firmware && idf.py reconfigure

server-config: ## Generate the gitignored server config; PROFILE=local (default) or minimax
	python3 tools/gen_server_config.py --profile $(or $(PROFILE),local) $(if $(FORCE),--force)

server-run: ## Start the server with the generated config; local services run separately
	cd $(SERVER_DIR) && $(SERVER_PY) app.py

screen-viewer: ## Watch Elena's screen; set ROBOT, optional ARGS (see docs/screen-stream.md)
	python3 tools/screen_viewer.py view --robot "$(ROBOT)" $(ARGS)
