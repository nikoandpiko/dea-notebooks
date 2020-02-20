## Virtual Product creation to test C3 datacube stats

#Need to link to custom install of dc-stats and dc-core:
# export PYTHONUSERBASE=/g/data/r78/cb3058/python_lib
# export PYTHONPATH=$PYTHONUSERBASE/lib/python3.6/site-packages:$PYTHONPATH
# export PATH=$PYTHONUSERBASE/bin:$PATH

from datacube.virtual import construct_from_yaml
from datacube import Datacube
from datacube.drivers.netcdf import create_netcdf_storage_unit, write_dataset_to_netcdf
from dask.distributed import LocalCluster, Client
import numpy as np
import xarray as xr

#user inputs
lat, lon = -33.2, 149.1
buffer = 0.05
time = ('1987', '2010')

#set up query
dc = Datacube(env='c3-samples')

query = {'lon': (lon - buffer, lon + buffer),
         'lat': (lat - buffer, lat + buffer),
         'time': time}

#create VP from yaml
# datacube_stats.external.ndvi_clim_mean
print('constructing from yaml')
ndvi_clim_mean = construct_from_yaml("""
        aggregate: datacube_stats.external.ndvi_clim_std
        group_by: alltime
        input:
          reproject:
            output_crs: EPSG:3577
            resolution: [-30, 30]
            resampling: average
          input:
            collate:
              - transform: apply_mask
                mask_measurement_name: fmask
                dilation: 3
                input:
                  transform: expressions
                  output: 
                    fmask:
                        formula: (fmask != 2) & (fmask != 3) & (fmask != 0) & (oa_nbart_contiguity == 1)
                        nodata: False
                    nbart_red: nbart_red
                    nbart_nir: nbart_nir
                  input:
                    product: ga_ls7e_ard_3
                    measurements: [nbart_red, nbart_nir, fmask, oa_nbart_contiguity]
                    gqa_iterative_mean_xy: [0, 1]
                    dataset_predicate: datacube_stats.main.ls7_on
              - transform: apply_mask
                mask_measurement_name: fmask
                dilation: 3
                input:
                  transform: expressions
                  output: 
                    fmask:
                        formula: (fmask != 2) & (fmask != 3) & (fmask != 0) & (oa_nbart_contiguity == 1)
                        nodata: False
                    nbart_red: nbart_red
                    nbart_nir: nbart_nir
                  input:
                    product: ga_ls5t_ard_3
                    measurements: [nbart_red, nbart_nir,fmask, oa_nbart_contiguity]
                    gqa_iterative_mean_xy: [0, 1]
                    dataset_predicate: datacube_stats.main.ls5_on
    """)

#load the VP and export
datasets = ndvi_clim_mean.query(dc, **query)
print(datasets)
print('actually computing...')
grouped = ndvi_clim_mean.group(datasets, **query)
results = ndvi_clim_mean.fetch(grouped, **query, dask_chunks={'time':-1, 'x':100, 'y':100})
results.load()

print('writing to file')
write_dataset_to_netcdf(results, 'VP_test_NDVI_climatology_1987_2010_std.nc')