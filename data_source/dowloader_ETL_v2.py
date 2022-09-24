import sqlite3
import time
from dagster import asset,repository,schedule,define_asset_job,sensor
from dagster import MetadataValue,DefaultScheduleStatus,Output,RunRequest,DefaultSensorStatus
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
            metadata_ = pd.read_sql('''select max(match_seq_num) as last_seq,
                                        max(start_time) as last_upd_date
                                        from matrix_table''',con=c)
            LAST_SEQ_STEP = metadata_.loc[0,'last_seq']
            last_upd = metadata_.loc[0,'last_upd_date']
    except:
        LAST_SEQ_STEP = int(5.6e+9)
        last_upd = pd.to_datetime('now',utc=True)

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
            

@asset(description='transform downloaded data',
        group_name='transform')
def optimize_data(context,get_response:dict)->pd.DataFrame:

    data = pd.DataFrame(get_response['result']['matches'])\
        .where(lambda x: x['human_players']==10)\
                .query('lobby_type in (0,7) and game_mode in (23,22,19)')\
                    .dropna(axis=1)

    match_table = pd.json_normalize(data['players'].explode('players')).dropna(axis=1)
    match_table[['radiant_win','match_id']] = data.explode('players')[['radiant_win','match_id']].values
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
                                    start_time=data['start_time'].median(),
                                    max_seq_num = data['match_seq_num'].max())






@asset(description='unload data to db',
        config_schema={"db_path": str},
        group_name='save')
def update_base(context,optimize_data:pd.DataFrame)->None:

    with sqlite3.connect(context.op_config['db_path']) as connect:
        optimize_data.to_sql('matrix_table',if_exists='append',index=False,con=connect)





    
update_matrix_data_job = define_asset_job(name='update_dota_matches',
                                        config={'ops':{"update_base": {"config": {"db_path": 'dotaIIbase.db'}},
                                                        "load_last_step": {"config": {"db_path": 'dotaIIbase.db'}},
                                                        "get_response": {"config": {"matches_requested": 100}}},
                                                })


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
    assets = [get_response,update_base,optimize_data,load_last_step]
    schedules = [dota_ddos_schedule,sensor_5_sec]
    jobs = [update_matrix_data_job]
    graphs = []
    return  assets  + jobs + schedules + graphs


if __name__=='__main__':
    n=0
    while 1==1:
        time.sleep(2)
        last_step = load_last_step(None)
        response = get_response(last_step)
        matrix_data = optimize_data(response)
        upd_db = update_base(matrix_data)
        print(f'{n}th step')
        n+=1

