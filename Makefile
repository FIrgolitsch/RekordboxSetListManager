.PHONY: help dev test lint typecheck check dist clean

help:
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | \
		awk 'BEGIN {FS = ":.*?## "}; {printf "  %-12s %s\n", $$1, $$2}'

dev: ## Install all dev + dist dependencies
	uv sync --all-extras

test: ## Run the test suite
	uv run pytest -q

lint: ## Run ruff linter
	uv run ruff check

typecheck: ## Run ty type-checker (src/ only)
	uv run ty check src/

check: lint typecheck test ## Run full quality gate (lint + types + tests)

dist: ## Build distributable via PyInstaller (auto-detects platform)
	uv run pyinstaller rekordbox_set_list_manager.spec --noconfirm
	@echo ""
	@python -c "import sys; name='RekordboxSetListManager'; \
exe=name+'.exe' if sys.platform=='win32' else name; \
bundle=name+'.app' if sys.platform=='darwin' else name; \
print('macOS: dist/'+bundle) if sys.platform=='darwin' else print('Windows: dist/'+exe) if sys.platform=='win32' else print('Linux: dist/'+name)"

# ── Code-signing (macOS, run after 'make dist') ────────────────────────────
# Replace TEAM_ID with your Apple Developer Team ID.
# Run only on macOS with Xcode Command Line Tools installed.
#
# codesign: ## Sign the .app bundle (requires TEAM_ID env var)
# 	codesign --deep --force --verify --verbose \
# 		--sign "Developer ID Application: Your Name ($(TEAM_ID))" \
# 		--entitlements entitlements.plist \
# 		dist/RekordboxSetListManager.app

clean: ## Remove build artefacts
	rm -rf build/ dist/ __pycache__ .pytest_cache
