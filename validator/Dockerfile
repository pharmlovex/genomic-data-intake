# Build from the repository root:
#   docker build -f validator/Dockerfile -t <region>.ocir.io/<namespace>/genome-intake/validator:0.1.0 .
FROM python:3.12-slim

RUN useradd --create-home --uid 10001 app
WORKDIR /app

COPY validator/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY core/intake_core ./intake_core
COPY validator/run_validation.py .

USER app
ENTRYPOINT ["python", "-u", "run_validation.py"]
