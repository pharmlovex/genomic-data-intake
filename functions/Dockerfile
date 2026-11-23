# Same layout `fn build` produces, so no Fn CLI is needed. Build from the repo root:
#   docker build --platform linux/amd64 -f functions/Dockerfile --build-arg FUNC=intake-api -t <image> .
FROM fnproject/python:3.11-dev AS build
ARG FUNC
WORKDIR /function
COPY functions/${FUNC}/requirements.txt .
RUN pip3 install --target /python/ --no-cache-dir -r requirements.txt
COPY functions/${FUNC}/func.py .
COPY core/intake_core ./intake_core

FROM fnproject/python:3.11
WORKDIR /function
COPY --from=build /python /python
COPY --from=build /function /function
ENV PYTHONPATH=/function:/python
ENTRYPOINT ["/python/bin/fdk", "/function/func.py", "handler"]
