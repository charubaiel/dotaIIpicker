FROM python:3.8-slim

ENV DAGSTER_HOME=/opt/dagster/dagster_home/

RUN mkdir -p /opt/dagster/dagster_home /opt/dagster/app
RUN touch /opt/dagster/dagster_home/dagster.yaml 

WORKDIR /opt/dagster/app

COPY ./data_source/creds.py ./data_source/dowloader_ETL_v2.py ./data_source/workspace.yaml /opt/dagster/app/
COPY poetry.lock pyproject.toml /opt/dagster/app/

RUN apt-get update
RUN pip install poetry

RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi

EXPOSE 3000

CMD ["/bin/bash","-c","dagit -h 0.0.0.0 -p 3000 & dagster-daemon run"]
