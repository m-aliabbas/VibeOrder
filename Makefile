# VibeOrder developer commands. Run from the repository root.
PY := backend/.venv/bin/python
MANAGE := cd backend && .venv/bin/python manage.py

.PHONY: setup db reset seed server worker sync backup drill kds app-web app-macos test test-backend test-app smoke analyze

setup:            ## venv + deps + DBs + migrate + seed
	cd backend && (test -d .venv || uv venv -q --python 3.13 .venv) && uv pip install -q --python .venv/bin/python -r requirements.txt
	-createdb vibeorder; -createdb vibeorder_test
	$(MANAGE) migrate -v0 && $(MANAGE) seed_demo
	cd app && flutter pub get

reset:            ## drop + recreate dev DB with fresh demo data
	-dropdb vibeorder
	createdb vibeorder
	$(MANAGE) migrate -v0 && $(MANAGE) seed_demo

seed:             ## add fresh pairing codes (idempotent for the rest)
	$(MANAGE) seed_demo

server:           ## branch API + KDS on :8000
	$(MANAGE) runserver 0.0.0.0:8000

worker:           ## durable print worker (spool files in backend/var/spool)
	$(MANAGE) print_worker

sync:             ## branch <-> cloud sync workers for B1 and B2 (dev credentials); storefront needs these
	$(MANAGE) sync_worker --branch B1 --dev & $(MANAGE) sync_worker --branch B2 --dev; wait

backup:           ## encrypted backup of the dev DB into backend/var/backups
	$(MANAGE) backup_branch

drill:            ## backup + restore drill into a scratch DB (verifies the backup)
	$(MANAGE) backup_branch --drill

app-web:          ## build staff app for web and serve on :8081
	cd app && flutter build web --release && python3 -m http.server 8081 --bind 127.0.0.1 --directory build/web

app-macos:        ## run staff app as a desktop app
	cd app && flutter run -d macos

test: test-backend test-app

test-backend:     ## pytest against PostgreSQL (incl. real concurrency tests)
	cd backend && .venv/bin/python -m pytest

test-app:         ## flutter analyze + widget tests
	cd app && flutter analyze && flutter test

smoke:            ## live end-to-end against a running server
	cd backend && .venv/bin/python scripts/smoke.py
