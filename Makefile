.PHONY: install test run docker-build clean

install:
	pip install -e ".[dev]"

test:
	python -m pytest

run:
	uvicorn omni.api.main:app --reload

docker-build:
	docker build -t omnimind:0.1.0 .

clean:
	rm -rf .pytest_cache **/__pycache__ *.egg-info
