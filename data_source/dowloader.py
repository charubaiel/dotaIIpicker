import sqlite3
from dagster import asset,repository,schedule,graph,define_asset_job
from dagster import MetadataValue,DefaultScheduleStatus,AssetSelection,Output
import pandas as pd 
import httpx as r
import numpy as np
from utils.creds import (STEAM_API_KEY,
                        GET_MATCH_HISTORY_BY_SEQ_NUM)



@asset(description='load last seq',
    config_schema={"db_path": str},
    group_name='download')
def load_last_step(context):
    try:
        with sqlite3.connect(context.op_config['db_path']) as c:
            metadata_ = pd.read_sql('select max(match_seq_num) as last_seq, max(start_time) as last_upd_date from general_stats_table',con=c)
            LAST_SEQ_STEP = metadata_.loc[0,'last_seq']
            last_upd = metadata_.loc[0,'last_upd_date']
    except:
        LAST_SEQ_STEP = 5.3e+9

    return Output(LAST_SEQ_STEP, metadata={'last_updated_match_date':
                                            MetadataValue.text(str(pd.to_datetime(last_upd,unit='s')))})



@asset(description='download data',
        config_schema={"matches_requested": int},
        group_name='download')
def get_response(context,load_last_step):

    fetch_params = {
                'key':STEAM_API_KEY,
                'matches_requested':context.op_config['matches_requested'],
                'start_at_match_seq_num':load_last_step,
                }

    pool_of_games = r.get(GET_MATCH_HISTORY_BY_SEQ_NUM,
                        params=fetch_params)

    if pool_of_games.status_code == 200:
        load_last_step+= np.ceil(context.op_config['matches_requested'] * 1.2)
        return pool_of_games
    else:
        context.warning(f'{pool_of_games.status_code}')
            


@asset(description='unload data to db',
        config_schema={"db_path": str},
        group_name='download')
def update_base(context,get_response):

    data = pd.DataFrame(get_response.json()['result']['matches'])\
        .where(lambda x: x['human_players']==10)\
                .query('lobby_type in (0,7) and game_mode in (23,22,19)')\
                    .dropna(axis=1)

    general_table = data.drop(['players'],axis=1).set_index('match_id')
            
    match_table = pd.json_normalize(data['players'].explode('players')).dropna(axis=1)
    match_table[['radiant_win','match_id']] = data.explode('players')[['radiant_win','match_id']].values
    match_table = match_table.set_index('match_id')

    with sqlite3.connect(context.op_config['db_path']) as connect:
        match_table.to_sql('match_table',if_exists='append',con=connect)
        general_table.to_sql('general_stats_table',if_exists='append',con=connect)
        ttl_row_info = pd.read_sql('select count(distinct match_id) as ttl_rows from general_stats_table',con=connect).loc[0,'ttl_rows']
        context.log.info(f'Updated_table total rows : {ttl_row_info}')

    return Output(ttl_row_info,metadata={'ttl_rows':int(ttl_row_info),
                                        'updated_rows':int(general_table.shape[0])})



@graph(description='update_dota_matches',
        config={"update_base": {"config": {"db_path": 'data_source/dota2base.db'}},
                "load_last_step": {"config": {"db_path": 'data_source/dota2base.db'}},
                "get_response": {"config": {"matches_requested": 500}}},
                
                )
def update_match_graph():
    update_base(get_response(load_last_step()))
    return




download_asset_job = define_asset_job(name='update_dota_matches',
                                        config={'ops':{"update_base": {"config": {"db_path": 'data_source/dota2base.db'}},
                                                        "load_last_step": {"config": {"db_path": 'data_source/dota2base.db'}},
                                                        "get_response": {"config": {"matches_requested": 100}}},
                                                },
                                        selection=AssetSelection.groups("download")
                                    )



@schedule(job=download_asset_job,
            cron_schedule="* * * * *",
            execution_timezone="Europe/Moscow",
            default_status=DefaultScheduleStatus.RUNNING
)
def dota_ddos_schedule():

    return {}





@repository
def dota_picker():
    assets = [get_response,update_base,load_last_step]
    graphs = [update_match_graph]
    schedules = [dota_ddos_schedule]
    jobs = [download_asset_job]
    return  assets  + jobs + schedules
