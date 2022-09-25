import sqlite3
from dagster import asset,repository,define_asset_job,sensor,schedule
from dagster import MetadataValue,Output,RunRequest,DefaultSensorStatus,DefaultScheduleStatus
import pandas as pd 
import requests as r
import numpy as np
from creds import (STEAM_API_KEY,
                    GET_MATCH_HISTORY_BY_SEQ_NUM)



@asset(description='load last seq',
    config_schema={"db_path": str},
    group_name='download')
def load_last_step(context):
    try:
        with sqlite3.connect(context.op_config['db_path']) as c:
            metadata_ = pd.read_sql('''select max(max_seq_num) as last_seq,
                                        max(start_time) as last_upd_date
                                        from INTEL_matrix_table''',con=c).astype(int)
            LAST_SEQ_STEP = int(metadata_.loc[0,'last_seq'])
            last_upd = int(metadata_.loc[0,'last_upd_date'])
    except:
        LAST_SEQ_STEP = int(5.45e+9)
        last_upd = 0

    return Output(LAST_SEQ_STEP, metadata={'last_updated_match_date':
                                            MetadataValue.text(str(pd.to_datetime(last_upd,unit='s')))})



@asset(description='download data',
        config_schema={"matches_requested": int},
        group_name='download')
def get_response(context,load_last_step:int)->dict:

    fetch_params = {
                'key':STEAM_API_KEY,
                'matches_requested':context.op_config['matches_requested'],
                'start_at_match_seq_num':load_last_step,
                }

    pool_of_games = r.get(GET_MATCH_HISTORY_BY_SEQ_NUM,
                            params=fetch_params)

    if pool_of_games.status_code == 200:
        load_last_step+= np.ceil(context.op_config['matches_requested'] * 1.2)
        return pool_of_games.json()
    else:
        context.log.warning(f'{pool_of_games.status_code} - {pool_of_games.text}')
            

@asset(description='raw json --> pd.DataFrame',
        group_name='transform'
        )
def prepare_data(context,get_response:dict)->pd.DataFrame:

    data = pd.DataFrame(get_response['result']['matches'])\
        .where(lambda x: x['human_players']==10)\
                .query('lobby_type in (0,7) and game_mode in (23,22,19)')\
                    .dropna(axis=1)

    return data


@asset(description='pd.DataFrame --> win\lose matrix',
        group_name='transform'
        )
def optimize_data(context,prepare_data:pd.DataFrame)->pd.DataFrame:

    match_table = pd.json_normalize(prepare_data['players'].explode('players')).drop(['account_id','leaver_status'],axis=1).dropna(axis=1).apply(pd.to_numeric,errors='coerce',downcast='unsigned').dropna(axis=1)
    match_table[['radiant_win','match_id']] = prepare_data.explode('players')[['radiant_win','match_id']].values
    match_table = match_table.set_index('match_id')

    hero_matrix = match_table\
    .groupby(['match_id','team_number','radiant_win'])\
        .agg({'hero_id':set})\
            .unstack(1)\
                .droplevel(0,axis=1)\
                    .explode([0,1])\
                        .reset_index(1)\
                            .pivot_table('radiant_win',0,1,aggfunc =['count','sum'])
    
    long_type_matrix = hero_matrix.melt(ignore_index=False).reset_index()
    long_type_matrix.columns = ['win_team','stats_type','lose_team','value']

    return long_type_matrix.assign(
                                    start_time=prepare_data['start_time'].median(),
                                    max_seq_num = prepare_data['match_seq_num'].max())




@asset(description='update raw db data',
        config_schema={"db_path": str},
        group_name='save')
def update_raw(context,prepare_data:pd.DataFrame):
    result_exploded_data = pd.json_normalize(prepare_data['players'].explode('players')).drop(['account_id','leaver_status'],axis=1).dropna(axis=1).apply(pd.to_numeric,errors='coerce',downcast='unsigned').dropna(axis=1)
    with sqlite3.connect(context.op_config['db_path']) as connect:
        result_exploded_data.to_sql('RAW_stats_table',if_exists='append',index=False,con=connect)
        ttl_rows = pd.read_sql('select count() from RAW_stats_table',con=connect).iloc[0,0]
    return Output(None, metadata={'match_updated':
                                    MetadataValue.int(int(ttl_rows))})



@asset(description='update h5 result matrix',
        config_schema={"db_path": str,"file_path": str},
        group_name='save')
def update_optimized_base(context,optimize_data):

    with sqlite3.connect(context.op_config['db_path']) as connect:
        optimize_data.to_sql('INTEL_matrix_table',if_exists='append',index=False,con=connect)
        total_data = pd.read_sql('select win_team,stats_type,lose_team,SUM(value) as stats from INTEL_matrix_table group by 1,2,3',con=connect)
        total_data.to_hdf(context.op_config['file_path'],key='matrix_table',mode='w',complevel=5,index=False)

    return Output(value=None,
                metadata = {
                            'MatrixFileSize':MetadataValue.float(total_data.memory_usage(deep=True).sum() / 1024**2),
                            'Min_matches':MetadataValue.float(optimize_data['value'].min()),
                            'Max_matches':MetadataValue.float(optimize_data['value'].max()),
                            }
                )

    
update_matrix_data_job = define_asset_job(name='update_dota_matches',
                                        config={'ops':{"update_raw": {"config": {"db_path": 'dbs/dotaIIbase.db'}},
                                                        "update_optimized_base": {"config": {"db_path": 'dbs/dotaIIbase.db','file_path':'dbs/optimized_matrix.h5'}},
                                                        "load_last_step": {"config": {"db_path": 'dbs/dotaIIbase.db'}},
                                                        "get_response": {"config": {"matches_requested": 100}}},
                                                },
                                        tags={"dagster/max_retries": 3, "dagster/retry_strategy": "ALL_STEPS"})


@schedule(job=update_matrix_data_job,
            cron_schedule="* * * * *",
            execution_timezone="Europe/Moscow",
            default_status=DefaultScheduleStatus.RUNNING
)
def dota_ddos_schedule():
    return {}


@sensor(job=update_matrix_data_job,
        minimum_interval_seconds=5,
        default_status=DefaultSensorStatus.RUNNING)
def sensor_5_sec():
    yield RunRequest(run_key=None, run_config={})




    
@repository
def dota_picker():
    assets = [get_response,
                update_raw,
                prepare_data,
                optimize_data,
                update_optimized_base,
                load_last_step]

    schedules = [sensor_5_sec,dota_ddos_schedule]
    jobs = [update_matrix_data_job]
    graphs = []
    return  assets  + jobs + schedules + graphs



