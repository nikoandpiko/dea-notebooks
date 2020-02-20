#!/usr/bin/env python
# coding: utf-8

# # MAHTS stats

# ## Background
# 

# ## Description
# 
# 

# ## Getting started
# 

# ### Load packages
# 
# First we import the required Python packages, then we connect to the database, and load the catalog of virtual products.

# In[2]:


# pip install --user ruptures

import os
import sys
import glob
import shutil
import numpy as np
import xarray as xr
import pandas as pd
import geopandas as gpd
import ruptures as rpt
from scipy import stats
from affine import Affine
import matplotlib.pyplot as plt
from shapely.wkt import loads
from shapely.ops import nearest_points
from rasterio.features import rasterize
from skimage.morphology import disk
from skimage.morphology import square
from skimage.morphology import binary_opening
from skimage.morphology import binary_dilation

sys.path.append('../Scripts')
from dea_spatialtools import largest_region
from dea_spatialtools import subpixel_contours
from dea_spatialtools import largest_region
from dea_plotting import map_shapefile


def change_regress(row, 
                   x_vals, 
                   x_labels, 
                   std_dev=3, 
                   detrend_params=None,
                   slope_var='slope', 
                   interc_var='intercept',
                   pvalue_var='pvalue', 
                   outliers_var='outliers'):
    
    # Extract x (time) and y (distance) values
    x = x_vals
    y = row.values.astype(np.float)
    
    # Drop NAN rows
    xy_df = np.vstack([x, y]).T
    is_valid = ~np.isnan(xy_df).any(axis=1)
    xy_df = xy_df[is_valid]
    valid_labels = x_labels[is_valid]
    
    # If detrending parameters are provided, apply these to the data to
    # remove the trend prior to running the regression
    if detrend_params:
        xy_df[:,1] = xy_df[:,1]-(detrend_params[0]*xy_df[:,0]+detrend_params[1])    
    
    # Remove outliers
    outlier_bool = (np.abs(stats.zscore(xy_df)) < float(std_dev)).all(axis=1)
    xy_df = xy_df[outlier_bool]
        
    # Compute linear regression
    lin_reg = stats.linregress(x=xy_df[:,0], 
                               y=xy_df[:,1])  
       
    # Return slope, p-values and list of outlier years excluded from regression   
    return pd.Series({slope_var: np.round(lin_reg.slope, 3), 
                      interc_var: np.round(lin_reg.intercept, 3),
                      pvalue_var: np.round(lin_reg.pvalue, 3),
                      outliers_var: ' '.join(map(str, valid_labels[~outlier_bool]))})


def breakpoints(x, labels, model='rbf', pen=10, min_size=2, jump=1):
    '''
    Takes an array of erosion values, and returns a list of 
    breakpoint years
    '''
    signal = x.values
    algo = rpt.Pelt(model=model, min_size=min_size, jump=jump).fit(signal)
    result = algo.predict(pen=pen)
    if len(result) > 1:
        return [labels[i] for i in result[0:-1]][0]
    else:
        return None

    
def mask_ocean(bool_array, connectivity=1):
    '''
    Identifies ocean by selecting the largest connected area of water
    pixels, then dilating this region by 1 pixel to include mixed pixels
    '''
    
    ocean_mask = largest_region(bool_array, connectivity=connectivity)
    ocean_mask = binary_dilation(ocean_mask, selem=square(3))

    return ocean_mask


def main(argv=None):

    if argv is None:

        argv = sys.argv
        print(sys.argv)

    # If no user arguments provided
    if len(argv) < 2:

        str_usage = "You must specify a study area ID"
        print(str_usage)
        sys.exit()
        
    # Set study area for analysis
    study_area = argv[1] 

    # ## Load in data

    # Read in contours
    water_index = 'mndwi'
    index_threshold = 0.00

    # Create output folder
    output_dir = f'output_data/{study_area}/vectors/'
    os.makedirs(output_dir, exist_ok=True)

    # Get file paths
    gapfill_index_files = sorted(glob.glob(f'output_data/{study_area}/gapfill_{water_index}_*.tif'))
    gapfill_tide_files = sorted(glob.glob(f'output_data/{study_area}/gapfill_tide_m_*.tif'))
    index_files = sorted(glob.glob(f'output_data/{study_area}/{water_index}_*.tif'))[1:len(gapfill_index_files)+1]
    stdev_files = sorted(glob.glob(f'output_data/{study_area}/stdev_*.tif'))[1:len(gapfill_index_files)+1]
    tidem_files = sorted(glob.glob(f'output_data/{study_area}/tide_m_*.tif'))[1:len(gapfill_index_files)+1]
    count_files = sorted(glob.glob(f'output_data/{study_area}/count_*.tif'))[1:len(gapfill_index_files)+1]

    # Create variable used for time axis
    time_var = xr.Variable('year', [int(i[-8:-4]) for i in index_files])

    # Import data
    index_da = xr.concat([xr.open_rasterio(i) for i in index_files], dim=time_var)
    gapfill_index_da = xr.concat([xr.open_rasterio(i) for i in gapfill_index_files], dim=time_var)
    gapfill_tide_da = xr.concat([xr.open_rasterio(i) for i in gapfill_tide_files], dim=time_var)
    stdev_da = xr.concat([xr.open_rasterio(i) for i in stdev_files], dim=time_var)
    tidem_da = xr.concat([xr.open_rasterio(i) for i in tidem_files], dim=time_var)
    count_da = xr.concat([xr.open_rasterio(i) for i in count_files], dim=time_var)

    # Assign names to allow merge
    index_da.name = water_index
    gapfill_index_da.name = 'gapfill_index'
    gapfill_tide_da.name = 'gapfill_tide_m'
    stdev_da.name = 'stdev'
    tidem_da.name = 'tide_m'
    count_da.name = 'count'

    # Combine into a single dataset and set CRS
    yearly_ds = xr.merge([index_da, gapfill_index_da, gapfill_tide_da, stdev_da, tidem_da, count_da]).squeeze('band', drop=True)
    yearly_ds.attrs['crs'] = index_da.crs
    yearly_ds.attrs['transform'] = Affine(*index_da.transform)

    # Print
    yearly_ds


    # ## Extract shoreline contours
    # 
    # ### Extract ocean-masked contours

    # In[5]:


    # Mask to study area
    comp_gdf = gpd.read_file('input_data/Euc_SCC_coast10kmbuffer.geojson').set_index('ID_Seconda')
    study_area_mask = rasterize(shapes=comp_gdf.to_crs(str(yearly_ds.crs)).loc[study_area]['geometry'],
                             out_shape=yearly_ds[water_index].shape[1:],
                             transform=yearly_ds.transform,
                             all_touched=True).astype(bool)

    # Remove low obs and high variance pixels and replace with 3-year gapfill
    gapfill_mask = (yearly_ds['count'] > 5) & (yearly_ds['stdev'] < 0.5)
    yearly_ds[water_index] = yearly_ds[water_index].where(gapfill_mask, other=yearly_ds.gapfill_index)
    yearly_ds['tide_m'] = yearly_ds['tide_m'].where(gapfill_mask, other=yearly_ds.gapfill_tide_m)

    # Apply water index threshold
    thresholded_ds = (yearly_ds[water_index] > index_threshold)
    thresholded_ds = thresholded_ds.where(~yearly_ds[water_index].isnull())

    # Load estuary mask
    # bbox = gpd.GeoSeries(loads(yearly_ds.geobox.extent.wkt), crs=yearly_ds.crs)
    from shapely.geometry import box
    bbox = gpd.GeoSeries(box(yearly_ds.x.min().item(), 
                             yearly_ds.y.min().item(), 
                             yearly_ds.x.max().item(), 
                             yearly_ds.y.max().item()), crs=yearly_ds.crs)
    estuary_gdf = (gpd.read_file('./input_data/estuary_mask.shp', bbox=bbox)
                   .to_crs(yearly_ds.crs))

    # Rasterize estuary polygons into a numpy mask. The try-except catches cases
    # where no estuary polygons exist in the study area
    try:
        estuary_mask = rasterize(shapes=estuary_gdf['geometry'],
                                 out_shape=yearly_ds[water_index].shape[1:],
                                 transform=yearly_ds.transform,
                                 all_touched=True).astype(bool)
    except:
        estuary_mask = np.full(yearly_ds[water_index].shape[1:], False, dtype=bool)

    # Drop empty timesteps and apply estuary mask
    thresholded_ds = (thresholded_ds
                      .sel(year=thresholded_ds.sum(dim=['x', 'y']) > 0)
                      .where(~estuary_mask, 0))

    # Identify ocean by identifying the largest connected area of water pixels
    # as water in at least 90% of the entire stack of thresholded data
    all_time_median = (thresholded_ds.mean(dim='year') > 0.9)
    full_sea_mask = mask_ocean(binary_opening(all_time_median, disk(3)))

    # Generate all time 750 m buffer from ocean-land boundary
    buffer_ocean = binary_dilation(full_sea_mask, disk(25))
    buffer_land = binary_dilation(~full_sea_mask, disk(25))
    coastal_buffer = buffer_ocean & buffer_land

    # # Generate sea mask for each timestep
    yearly_sea_mask = thresholded_ds.groupby('year').apply(mask_ocean)

    # Keep only pixels that are within 750 m of the ocean in the
    # full stack, and directly connected to ocean in each yearly timestep
    masked_ds = yearly_ds[water_index].where(yearly_sea_mask & coastal_buffer)

    # Restrict to study area polygon
    masked_ds = masked_ds.where(study_area_mask)

    # Set CRS and trasnform from input data
    masked_ds.attrs['crs'] = yearly_ds.crs[6:]
    masked_ds.attrs['transform'] = yearly_ds.transform

    # Extract contours
    contour_path = f'output_data/{study_area}/vectors/{study_area}_contours_{water_index}_{index_threshold:.2f}'
    contours_gdf = subpixel_contours(da=masked_ds,
                                     z_values=index_threshold,
                                     output_path=f'{contour_path}.geojson',
                                     min_vertices=10,
                                     dim='year')

    # Plot
    # contours_gdf.plot(column='year', cmap='YlOrRd')


    # ## Compute statistics
    # ### Measure distances from baseline

    # In[6]:


    # Get array of water index values for baseline time period 
    baseline_year = '2018'
    baseline_array = yearly_ds[water_index].sel(year=int(baseline_year))

    # Import contours and project to local CRS
    # contours_gdf = contours_clean_gdf
    contours_index_gdf = contours_gdf.set_index('year')

    # Set annual shoreline to use as a baseline
    baseline_contour = contours_index_gdf.loc[[baseline_year]].geometry

    # Generate points along line and convert to geopandas.GeoDataFrame
    points_line = [baseline_contour.iloc[0].interpolate(i) 
                   for i in range(0, int(baseline_contour.length), 30)]
    points_gdf = gpd.GeoDataFrame(geometry=points_line, crs=baseline_array.crs)

    # Make a copy of the GeoDataFrame to hold tidal data
    tide_points_gdf = points_gdf.copy()
    # gapfill_points_gdf = points_gdf.copy()


    # In[7]:


    # Copy geometry to baseline point
    points_gdf['p_baseline'] = points_gdf.geometry
    baseline_x_vals = points_gdf.geometry.x
    baseline_y_vals = points_gdf.geometry.y

    # Iterate through all comparison years in contour gdf
    for comp_year in contours_index_gdf.index.unique().values[0:32]:

        print(comp_year)

        # Set comparison contour
        comp_contour = contours_index_gdf.loc[[comp_year]].geometry.iloc[0]

        # Find nearest point on comparison contour
        points_gdf[f'p_{comp_year}'] = points_gdf.apply(lambda x: 
                                                        nearest_points(x.p_baseline, comp_contour)[1], axis=1)

        # Compute distance between baseline and comparison year points
        points_gdf[f'{comp_year}'] = points_gdf.apply(lambda x: 
                                                      x.geometry.distance(x[f'p_{comp_year}']), axis=1)

        # Extract comparison array
        comp_array = yearly_ds[water_index].sel(year=int(comp_year))

        # Convert baseline and comparison year points to geoseries to allow easy access to x and y coords
        comp_x_vals = gpd.GeoSeries(points_gdf[f'p_{comp_year}']).x
        comp_y_vals = gpd.GeoSeries(points_gdf[f'p_{comp_year}']).y

        # Sample NDWI values from arrays based on baseline and comparison points
        baseline_x_vals = xr.DataArray(baseline_x_vals, dims='z')
        baseline_y_vals = xr.DataArray(baseline_y_vals, dims='z')
        comp_x_vals = xr.DataArray(comp_x_vals, dims='z')
        comp_y_vals = xr.DataArray(comp_y_vals, dims='z')   
        points_gdf['index_comp_p1'] = comp_array.interp(x=baseline_x_vals, y=baseline_y_vals)
        points_gdf['index_baseline_p2'] = baseline_array.interp(x=comp_x_vals, y=comp_y_vals)

        # Compute directionality of change (negative = erosion, positive = accretion)    
        points_gdf['loss_gain'] = np.where(points_gdf.index_baseline_p2 > 
                                           points_gdf.index_comp_p1, 1, -1)
        points_gdf[f'{comp_year}'] = points_gdf[f'{comp_year}'] * points_gdf.loss_gain

        # Add tide data
        tide_array = yearly_ds['tide_m'].sel(year=int(comp_year))
        tide_points_gdf[f'{comp_year}'] = tide_array.interp(x=baseline_x_vals, y=baseline_y_vals)

    #     # Add gapfill data
    #     gapfill_array = gapfill_mask.sel(year=int(comp_year))
    #     gapfill_points_gdf[f'{comp_year}'] = gapfill_array.astype(int).interp(x=comp_x_vals, 
    #                                                                           y=comp_y_vals, 
    #                                                                           method='nearest')

    # Keep required columns
    points_gdf = points_gdf[['geometry'] + 
                            contours_index_gdf.index.unique().values.tolist()]
    points_gdf = points_gdf.round(2)

    # Zero values to 1988
    points_gdf.iloc[:,1:] = points_gdf.iloc[:,1:].subtract(points_gdf['1988'], axis=0)

    # Identify dates for regression
    x_years = np.array([int(i[:4]) for i in points_gdf.columns[1:]])


    # ### Calculate regressions

    # In[11]:


    # Identify SOI values for regression
    climate_df = pd.read_csv('input_data/climate_indices.csv', index_col='year')
    climate_df = climate_df.loc[x_years,:]

    # Compute change rates
    rate_out = points_gdf[x_years.astype(str)].apply(lambda x: change_regress(row=x, 
                                                         x_vals = x_years, 
                                                         x_labels = x_years, 
                                                         std_dev=3), axis=1)
    points_gdf[['rate_time', 'incpt_time', 'sig_time', 'outl_time']] = rate_out


    # Compute tide flag
    # tide_out = tide_points_gdf[x_years.astype(str)].apply(lambda x: change_regress(row=x, 
    #                                                x_vals = x_years, 
    #                                                x_labels = x_years, 
    #                                                std_dev=3), axis=1)
    tide_out = tide_points_gdf[x_years.astype(str)].apply(lambda x: change_regress(row=points_gdf[x_years.astype(str)].iloc[x.name], 
                                                   x_vals=x, 
                                                   x_labels=x_years, 
                                                   std_dev=3), axis=1)
    points_gdf[['rate_tide', 'incpt_tide', 'sig_tide', 'outl_tide']] = tide_out 


    # Compute stats for each index
    for ci in climate_df:

        print(ci)

        # Compute stats for each row
        ci_out = points_gdf[x_years.astype(str)].apply(lambda x: change_regress(row=x,
                                                           x_vals = climate_df[ci].values, 
                                                           x_labels = x_years, 
    #                                                        detrend_params=[x.rate_time, x.incpt_time],
                                                           std_dev=3), axis=1)

        # Add data as columns  
        points_gdf[[f'rate_{ci}', f'incpt_{ci}', f'sig_{ci}', f'outl_{ci}']] = ci_out


    # # Add breakpoints
    # print('Identifying breakpoints')
    # points_gdf['breakpoint'] = points_gdf.apply(lambda x: breakpoints(x=x[x_years.astype(str)], 
    #                                                                   labels=x_years, 
    #                                                                   pen=10), axis=1)

    # Set CRS
    points_gdf.crs = baseline_array.crs

    # Custom sorting
    points_towrite = points_gdf.loc[:, [
        'rate_time', 'rate_SOI', 'rate_IOD', 'rate_SAM', 'rate_IPO', 'rate_PDO', 'rate_tide',
        'sig_time', 'sig_SOI', 'sig_IOD', 'sig_SAM', 'sig_IPO', 'sig_PDO', 'sig_tide',
        'outl_time', 'outl_SOI', 'outl_IOD', 'outl_SAM', 'outl_IPO', 'outl_PDO', 'outl_tide',
    #     'breakpoint', 
        *x_years.astype(str).tolist(), 'geometry'
    ]]

    # Export
    stats_path = f'output_data/{study_area}/vectors/{study_area}_stats_{water_index}_{index_threshold}'
    points_towrite.to_file(f'{stats_path}.geojson', driver='GeoJSON')


    # ## Shapefile package

    # In[ ]:


    contours_gdf.to_file(f'{contour_path}.shp')
    points_towrite.to_file(f'{stats_path}.shp')

    shutil.make_archive(base_name=f'output_data/outputs_{study_area}_tidetest', 
                        format='zip', 
                        root_dir=f'output_data/{study_area}/vectors/')



if __name__ == "__main__":
    main()