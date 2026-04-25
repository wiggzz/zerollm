.PHONY: setup setup-dev sync-requirements ami-build ami-build-deploy ami-build-start ami-build-latest ami-prune test test-unit test-e2e build deploy validate clean seed-models seed-models-upload create-api-key logs status

STACK_NAME   ?= diogenes
ENVIRONMENT  ?= dev
AWS_REGION   ?= $(shell aws configure get region 2>/dev/null)
MODEL        ?=
LINES        ?= 60
MODELS_BUCKET ?=

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

ami-prune:
	AWS_REGION="$(AWS_REGION)" KEEP="$(KEEP)" ./ami/imagebuilder.sh prune

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
	AWS_REGION="$(AWS_REGION)" STACK_NAME="$(STACK_NAME)" uv run --no-sync python scripts/seed_models.py

seed-models-upload:
	AWS_REGION="$(AWS_REGION)" STACK_NAME="$(STACK_NAME)" MODELS_BUCKET="$(MODELS_BUCKET)" uv run --extra upload python scripts/seed_models.py --upload

create-api-key:
	@test -n "$(EMAIL)" || (echo "Usage: make create-api-key EMAIL=you@example.com" && exit 1)
	AWS_REGION="$(AWS_REGION)" uv run --no-sync python scripts/create_api_key.py --email "$(EMAIL)"

logs:
	AWS_REGION="$(AWS_REGION)" ENVIRONMENT="$(ENVIRONMENT)" MODEL_FILTER="$(MODEL)" LINES="$(LINES)" ./scripts/instance-logs.sh

status:
	AWS_REGION="$(AWS_REGION)" ENVIRONMENT="$(ENVIRONMENT)" ./scripts/cluster-status.sh

clean:
	rm -rf .aws-sam/
