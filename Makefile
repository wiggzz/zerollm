.PHONY: setup setup-dev sync-requirements ami-build ami-build-deploy ami-build-start ami-build-latest test test-unit test-e2e build deploy validate clean seed-models create-api-key

STACK_NAME ?= diogenes

setup:
	uv sync

setup-dev:
	uv sync --extra dev

sync-requirements:
	uv export --no-dev --no-hashes --no-header --output-file requirements.txt

ami-build:
	./ami/imagebuilder.sh build

ami-build-deploy:
	./ami/imagebuilder.sh deploy

ami-build-start:
	./ami/imagebuilder.sh start

ami-build-latest:
	./ami/imagebuilder.sh latest

test: test-unit

test-unit:
	uv run --no-sync pytest tests/unit/ -v

test-e2e:
	uv run --no-sync pytest tests/e2e/ -v

build: sync-requirements
	sam build

deploy:
	STACK_NAME="$(STACK_NAME)" ./scripts/deploy.sh

validate:
	sam validate

seed-models:
	AWS_REGION="$(AWS_REGION)" uv run --no-sync python scripts/seed_models.py

create-api-key:
	@test -n "$(EMAIL)" || (echo "Usage: make create-api-key EMAIL=you@example.com" && exit 1)
	AWS_REGION="$(AWS_REGION)" uv run --no-sync python scripts/create_api_key.py --email "$(EMAIL)"

clean:
	rm -rf .aws-sam/
