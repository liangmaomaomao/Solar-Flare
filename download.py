import re
import os
from glob import glob
from multiprocessing import Pool
import multiprocessing
from datetime import datetime, timedelta
import pandas as pd
from tqdm import tqdm, trange
import drms
from sunpy.time import TimeRange
from sunpy.net import Fido
from sunpy.net import attrs as a

######### Change these ##########
EMAIL = 'lixinyu@umich.edu
RAW_DATA_DIR = '/data2'
#################################


c = drms.Client(debug=False, verbose=False, email=EMAIL)
SHARP_HEADER_DIR = os.path.join(RAW_DATA_DIR, 'SHARP/header')
SHARP_IMAGE_DIR = os.path.join(RAW_DATA_DIR, 'SHARP/image')
SMARP_HEADER_DIR = os.path.join(RAW_DATA_DIR, 'SMARP/header')
SMARP_IMAGE_DIR = os.path.join(RAW_DATA_DIR, 'SMARP/image')
GOES_DIR = os.path.join(RAW_DATA_DIR, 'GOES')
for folder in [SHARP_HEADER_DIR, SHARP_IMAGE_DIR,
               SMARP_HEADER_DIR, SMARP_IMAGE_DIR,
               GOES_DIR]:
    if not os.path.exists(folder):
        os.makedirs(folder)


def download_sharp_headers(harpnum):
    filename = os.path.join(SHARP_HEADER_DIR, f'HARP{harpnum:06d}_ATTRS.csv')
    if os.path.exists(filename):
        # Header exists
        return 1

    keys = c.query(f'hmi.sharp_cea_720s[{harpnum}][][? (QUALITY<65536) ?][? (NOAA_NUM>=1) ?]', 
                   key=drms.const.all)
    if len(keys) == 0:
        # Header has no record subject to the constraints
        return 2

    t_rec = drms.to_datetime(keys['T_REC'])
    d = t_rec[0].date()
    start_date = datetime(d.year, d.month, d.day)
    delta = timedelta(minutes=96)
    mod = (t_rec - start_date) % delta
    keys = keys[mod.dt.seconds == 0]
    if len(keys) == 0:
        # Header has no record aligned with MDI recording times
        return 3

    keys.to_csv(filename, index=None)

    return 0


def download_smarp_headers(tarpnum):
    filename = os.path.join(SMARP_HEADER_DIR, f'TARP{tarpnum:06d}_ATTRS.csv')
    if os.path.exists(filename):
        return 1

    keys = c.query(f'mdi.smarp_cea_96m[{tarpnum}][][? (QUALITY<262144) ?][? (NOAA_NUM>=1) ?]',
                   key=drms.const.all)
    if len(keys) == 0:
        return 2

    keys.to_csv(filename, index=None)
    return 0


def _filename_from_export_record(rs):
    """Adapted from drms"""
    _re_export_recset = re.compile(r'^\s*([\w\.]+)\s*(\[.*\])?\s*(?:\{([\w\s\.,]*)\})?\s*$')
    _re_export_recset_pkeys = re.compile(r'\[([^\[^\]]*)\]')
    _re_export_recset_slist = re.compile(r'[\s,]+')

    m = _re_export_recset.match(rs)
    sname, pkeys, segs = m.groups()
    pkeys = _re_export_recset_pkeys.findall(pkeys)
    segs = _re_export_recset_slist.split(segs)
    pkeys[1] = pkeys[1].replace('.', "").replace(':', "").replace('-', "")
    str_list = [sname] + pkeys + segs + ['fits']
    fname = '.'.join(str_list)
    return fname


def download_sharp_images(harpnum):
    header = os.path.join(SHARP_HEADER_DIR, f'HARP{harpnum:06d}_ATTRS.csv')
    if not os.path.exists(header):
        return 1

    image_dir = os.path.join(SHARP_IMAGE_DIR, f'{harpnum:06d}')
    if not os.path.exists(image_dir):
        os.makedirs(image_dir)

    df = pd.read_csv(header)
    t1, t2 = df['T_REC'].iloc[0], df['T_REC'].iloc[-1]
    request = c.export(f'hmi.sharp_cea_720s[{harpnum}][{t1}-{t2}@96m][? (QUALITY<65536) ?]{{magnetogram}}')
                       #method='url', protocol='fits') # can't be pickled by parallel pool
    if len(request.data) == 0:
        return 2

    downloaded = sorted([os.path.basename(f) for f in glob(os.path.join(image_dir, 'hmi*'))])
    filenames = request.data['record'].apply(_filename_from_export_record)
    idx = request.data[~filenames.isin(downloaded)].index
    if len(idx) == 0:
        return 3

    request.download(image_dir, index=idx)
    return request.data.loc[idx]

    
def download_smarp_images(tarpnum):
    header = os.path.join(SMARP_HEADER_DIR, f'TARP{tarpnum:06d}_ATTRS.csv')
    if not os.path.exists(header):
        return 1

    image_dir = os.path.join(SMARP_IMAGE_DIR, f'{tarpnum:06d}')
    if not os.path.exists(image_dir):
        os.makedirs(image_dir)

    df = pd.read_csv(header)
    t1, t2 = df['T_REC'].iloc[0], df['T_REC'].iloc[-1]
    request = c.export(f'mdi.smarp_cea_96m[{tarpnum}][{t1}-{t2}@96m][? (QUALITY<262144) ?]{{magnetogram}}')
    if len(request.data) == 0:
        return 2

    downloaded = sorted([os.path.basename(f) for f in glob(os.path.join(image_dir, 'mdi*'))])
    filenames = request.data['record'].apply(_filename_from_export_record)
    idx = request.data[~filenames.isin(downloaded)].index
    if len(idx) == 0:
        return 3

    request.download(image_dir, index=idx)
    return request.data.loc[idx]


def download_goes_per_year(year):
    print(year)
    t_start = datetime(year=year, month=1, day=1)
    t_end = datetime(year=year+1, month=1, day=1)
    results = Fido.search(
        a.Time(t_start, t_end),
        a.hek.EventType("FL"),
        # a.hek.FL.GOESCls > "M1.0",
        a.hek.OBS.Observatory == "GOES"
    )
    if not results.all_colnames: # no columns / no results
        return None

    event_table = results['hek']["event_starttime", "event_peaktime", "event_endtime", "fl_goescls", "ar_noaanum"]
    event_df = event_table.to_pandas().rename(columns={
        'event_starttime': 'start_time',
        'event_peaktime': 'peak_time',
        'event_endtime': 'end_time',
        'fl_goescls': 'goes_class',
        #'hgc_coord': 'goes_location',
        'ar_noaanum': 'noaa_active_region',
    })
    event_df = event_df[event_df['noaa_active_region'] != 0]
    if len(event_df) == 0:
        return None

    return event_df


def download_goes():
    # Up until 2021-05-21, the last record retrieved by sunpy is a B3.2 flare
    # at 2020-12-23T05:53:00 in AR 12795.
    year_range = range(1996, 2022)
    with Pool(4) as pool:
        df_list = pool.map(download_goes_per_year, year_range)
    print("These years don't have GOES events assigned with NOAA AR: {}".format(
          [year_range[i] for i, df in enumerate(df_list) if df is None]))

    goes = pd.concat(df_list)

    # Drop 61 records with nan class
    goes = goes[goes['goes_class'] != '']

    # Two C-class events has no scales, but we keep them anyway
    #goes = goes[goes['goes_class'] != 'C']

    goes.to_csv(os.path.join(GOES_DIR, 'goes.csv'), index=None)


def batch_run(func, max_num, batch_size, num_workers=8):
    """Parallelize func by batch with progress bar"""
    pbar = trange(0, max_num+1, batch_size)
    results = []
    for start in pbar:
        stop = min(start + batch_size, max_num+1)
        pbar.set_description('Batch [%d, %d) / %d' % (start, stop, max_num+1))
        batch = range(start, stop)
        with Pool(num_workers) as p:
            results.extend(p.map(func, batch))
    return results


def analyze(results, tag, images=False):
    if images:
        dfs = [i for i in results if not isinstance(i, int)]
        if len(dfs) > 0:
            pd.concat(dfs).reset_index(drop=True).to_csv('log_add_'+tag+'.csv')
        results = [i if isinstance(i, int) else 0 for i in results]

    df = pd.DataFrame(results, columns=['result'])
    df.to_csv('log_download_'+tag+'.csv')
    print(tag)
    print(df['result'].value_counts())


if __name__ == '__main__':
    download_goes()

    # Last record we consider: hmi.sharp_cea_720s[7544][2021.02.03_04:12:00_TAI]
    results = batch_run(download_sharp_headers, 7544, 100)
    analyze(results, 'sharp_headers')

    # SMARP has TARPNUM from 1 to 13670
    results = batch_run(download_smarp_headers, 14000, 1000)
    analyze(results, 'smarp_headers')

    results = batch_run(download_sharp_images, 7544, 100)
    analyze(results, 'sharp_images', images=True)

    results = batch_run(download_smarp_images, 14000, 1000)
    analyze(results, 'smarp_images', images=True)
