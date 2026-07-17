.PHONY: setup setup-sam3 dev up down lint test

setup:
	npm run setup

setup-sam3:
	npm run setup:sam3

dev:
	npm run dev

up:
	docker compose up --build

down:
	docker compose down

test:
	.venv/bin/python -m pytest tests
