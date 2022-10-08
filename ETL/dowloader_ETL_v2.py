import duckdb
from dagster import asset,repository,define_asset_job,sensor
from dagster import MetadataValue,Output,RunRequest,DefaultSensorStatus
import pandas as pd 
import requests as r
import numpy as np
from creds import (STEAM_API_KEY,
                    GET_MATCH_HISTORY_BY_SEQ_NUM)


GENERAL_MATCH_COLUMNS = ['player_slot', 'team_number', 'team_slot', 'hero_id', 'item_0',
                        'item_1', 'item_2', 'item_3', 'item_4', 'item_5', 'backpack_0',
                        'backpack_1', 'backpack_2', 'item_neutral', 'kills', 'deaths',
                        'assists', 'last_hits', 'denies', 'gold_per_min', 'xp_per_min',
                        'level', 'net_worth']


@asset(description='load last seq',
    config_schema={"db_path": str},
    group_name='download')
def load_last_step(context):

    with duckdb.connect(context.op_config['db_path']) as c:
        try:
            metadata_ = pd.read_sql('''select max(max_seq_num) as last_seq,
                                        max(start_time) as last_upd_date
                                        from INTEL.matrix_table''',con=c)
            
            LAST_SEQ_STEP = int(metadata_.loc[0,'last_seq'])
            last_upd = metadata_.loc[0,'last_upd_date']
         
        except:
            LAST_SEQ_STEP = int(5.6e+9)
            last_upd = 0

    return Output(LAST_SEQ_STEP, metadata={'last_updated_match_date':
                                            MetadataValue.text(str(last_upd))})



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

    data['start_time'] = pd.to_datetime(data['start_time'],unit='s').dt.date


    exploded_df = data.explode(column= 'players' )

    players_table = pd.json_normalize(exploded_df['players']).loc[:,GENERAL_MATCH_COLUMNS].dropna(axis=1)

    match_table = pd.concat([exploded_df.drop('players',axis=1).reset_index(drop=True)
                            ,players_table]
                            ,axis=1
                            )

    match_table['is_winner'] =  match_table['team_number'] != match_table['radiant_win']
    return match_table.set_index('match_id').apply(pd.to_numeric,errors='ignore',downcast='unsigned')




@asset(description='pd.DataFrame --> win\lose matrix',
        group_name='transform'
        )
def optimize_data(context,prepare_data:pd.DataFrame)->pd.DataFrame:

    filtred_errors_matches = prepare_data.reset_index().groupby(['match_id','is_winner'])['hero_id'].nunique().where(lambda x: x == 5).dropna().unstack().index
    hero_matrix = prepare_data.loc[filtred_errors_matches]\
                    .groupby(['match_id','team_number','radiant_win'])\
                        .agg({'hero_id':set})\
                            .unstack(1)\
                                .droplevel(0,axis=1)\
                                    .explode([0,1])\
                                        .reset_index(1)\
                                            .pivot_table('radiant_win',0,1,aggfunc =['count','sum'])
    
    long_type_matrix = hero_matrix.melt(ignore_index=False).reset_index()
    long_type_matrix.columns = ['win_team','stats_type','lose_team','value']
    optimized_data = long_type_matrix.assign(
                                    start_time=prepare_data['start_time'].max(),
                                    max_seq_num = prepare_data['match_seq_num'].max())

    optimized_data = optimized_data.loc[:,['start_time', 'win_team', 'stats_type', 'lose_team', 'value','max_seq_num']]
    return optimized_data
                            




@asset(description='update raw db data',
        config_schema={"file_path": str},
        group_name='save')
def update_raw(context,prepare_data:pd.DataFrame):
    prepare_data.to_hdf(context.op_config['file_path'],key='raw',mode='a',complevel=9,index=False)
    return Output(None, metadata={'uniq_heroes_matches':
                                    MetadataValue.int(int(prepare_data['hero_id'].nunique()))})



@asset(description='update h5 result matrix',
        config_schema={"db_path": str,"file_path": str},
        group_name='save')
def update_optimized_base(context,optimize_data):

    connect =  duckdb.connect(context.op_config['db_path'])
    # data in query reads directly from object optimize_data

    connect.execute(''' 
                    CREATE SCHEMA IF NOT EXISTS INTEL;
                    CREATE TABLE IF NOT EXISTS INTEL.matrix_table as select * from optimize_data TABLESAMPLE 0;
                    INSERT INTO INTEL.matrix_table select * from optimize_data;
                    ''')

    connect.execute('''
                    CREATE OR REPLACE TABLE INTEL.matrix_table
                    as 
                        select
                            start_time,
                            win_team,
                            stats_type,
                            lose_team,
                            SUM(value) as value,
                            max(max_seq_num) as max_seq_num 

                        from INTEL.matrix_table
                        group by 1,2,3,4
                ''')

    total_data = connect.execute('''
                                    select
                                        win_team,
                                        stats_type,
                                        lose_team,
                                        SUM(value) as stats
                                    from INTEL.matrix_table
                                    group by 1,2,3
                                ''').df()
    connect.close()


    return Output(value=None,
                metadata = {
                            'MatrixFileSize':MetadataValue.float(total_data.memory_usage(deep=True).sum() / 1024**2),
                            'Mean_matches':MetadataValue.float(total_data['stats'].where(lambda x: x!=0).mean()),
                            'STD_matches':MetadataValue.float(total_data['stats'].where(lambda x: x!=0).std()),
                            'Min_matches':MetadataValue.float(total_data['stats'].where(lambda x: x!=0).min()),
                            '25_quantile_matches':MetadataValue.float(total_data['stats'].where(lambda x: x!=0).quantile(.25)),
                            'Median':MetadataValue.float(total_data['stats'].where(lambda x: x!=0).quantile(.5)),
                            '75_quantile_matches':MetadataValue.float(total_data['stats'].where(lambda x: x!=0).quantile(.75)),
                            'Max_matches':MetadataValue.float(total_data['stats'].max()),
                            }
                )

    
update_matrix_data_job = define_asset_job(name='update_dota_matches',
                                        config={'ops':{"update_raw": {"config": {'file_path':'dbs/raw.h5'}},
                                                        "update_optimized_base": {"config": {"db_path": 'dbs/dotaIIbase.duckdb','file_path':'dbs/optimized_matrix.h5'}},
                                                        "load_last_step": {"config": {"db_path": 'dbs/dotaIIbase.duckdb'}},
                                                        "get_response": {"config": {"matches_requested": 100}}},
                                                },
                                        tags={"dagster/max_retries": 3, "dagster/retry_strategy": "ALL_STEPS"})



@sensor(job=update_matrix_data_job,
        minimum_interval_seconds=6,
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

    schedules = [sensor_5_sec]
    jobs = [update_matrix_data_job]

    return  assets  + jobs + schedules



