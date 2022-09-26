
# Микропроект для выкачки и аналитики пиков по Dota 2

1) ETL для обновления данных
    1. Прототип
    2. переход на DAG процесс
    3. Переход на полноценный шедульный docker  
    4. Оптимизация БД и таблиц
    ---- мы тут ----
    5. Разворачивание инфры для бесперебойной выкачки и доступа
2) Анализ и методология оценки эффективности
    1. Матрица героев
        1. Оценка статистики винрейта
        2. loglike подход к оценке вероятности победы  
            ---- мы тут ----
3) API проекта  
        ---- мы тут ----


#  Запуск ETL процесса по выгрузке данных.  
> by container
```
docker build -t dotaetl .
docker run \
    --name dotaetl_container \
    -p 3000:3000 \
    -e STEAM_API_KEY=$STEAM_API_KEY \
    -v $PWD/ETL/dbs:/opt/dagster/app/dbs \
    -d dotaetl
```

> by compose    

all params in .env

```
docker compose up -d

```
Морда дагстера на  localhost:3000 