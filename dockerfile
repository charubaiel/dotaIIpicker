FROM python:3.8-slim

ENV DAGSTER_HOME=/opt/dagster/dagster_home/
ENV STEAM_API_KEY='meh'
ENV DAGSTER_PORT=3000

RUN mkdir -p /opt/dagster/dagster_home /opt/dagster/app
RUN touch /opt/dagster/dagster_home/dagster.yaml 

WORKDIR /opt/dagster/app

COPY ./ETL/ /opt/dagster/app/
COPY poetry.lock pyproject.toml /opt/dagster/app/

RUN apt-get update
RUN pip install poetry

RUN poetry config virtualenvs.create false \
    && poetry install --no-interaction --no-ansi


CMD ["/bin/bash","-c","dagit -h 0.0.0.0 -p ${DAGSTER_PORT} & dagster-daemon run"]
