import sqlite3
import pandas as pd 
import httpx as r
import time
import numpy as np
from creds import (STEAM_API_KEY,
                    GET_MATCH_HISTORY_BY_SEQ_NUM)

DB_PATH = 'data_source/dota2base.db'



def load_last_step():
    try:
        with sqlite3.connect(DB_PATH) as c:
            metadata_ = pd.read_sql('''select 
                                    max(match_seq_num) as last_seq,
                                    max(start_time) as last_upd_date
                                    from general_stats_table''',
                                    con=c)
            LAST_SEQ_STEP = metadata_.loc[0,'last_seq']
            last_upd = metadata_.loc[0,'last_upd_date']
    except:
        LAST_SEQ_STEP = 5.5e+9

    return LAST_SEQ_STEP,last_upd



def get_response(load_last_step):

    fetch_params = {
                'key':STEAM_API_KEY,
                'matches_requested':100,
                'start_at_match_seq_num':load_last_step,
                }

    pool_of_games = r.get(GET_MATCH_HISTORY_BY_SEQ_NUM,
                        params=fetch_params)

    if pool_of_games.status_code == 200:
        load_last_step+= np.ceil(100 * 1.2)
        return pool_of_games
    else:
        print(f'{pool_of_games.status_code}')
            


def update_base(get_response):

    data = pd.DataFrame(get_response.json()['result']['matches'])\
        .where(lambda x: x['human_players']==10)\
                .query('lobby_type in (0,7) and game_mode in (23,22,19)')\
                    .dropna(axis=1)

    general_table = data.drop(['players'],axis=1).set_index('match_id')
            
    match_table = pd.json_normalize(data['players'].explode('players')).dropna(axis=1)
    match_table[['radiant_win','match_id']] = data.explode('players')[['radiant_win','match_id']].values
    match_table = match_table.set_index('match_id')

    with sqlite3.connect(DB_PATH) as connect:
        match_table.to_sql('match_table',if_exists='append',con=connect)
        general_table.to_sql('general_stats_table',if_exists='append',con=connect)
        ttl_row_info = pd.read_sql('select count(distinct match_id) as ttl_rows from general_stats_table',con=connect).loc[0,'ttl_rows']


    return ttl_row_info





if __name__=='__main__':
    n=0
    sleep_time = 1.75
    while 1==1:
        time.sleep(sleep_time)
        try:
            last_match_seq,last_load_date = load_last_step()
            rows_upd = update_base(get_response(last_match_seq))
        except:
            time.sleep(10)
            sleep_time+=.01
        print(f'{n}th step : {rows_upd} ttl_matches | last_loaded_match : {pd.to_datetime(last_load_date,unit="s")} | sleep time {sleep_time}')
        n+=1

