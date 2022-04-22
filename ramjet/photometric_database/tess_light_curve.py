"""
Code to represent a TESS light curve.
"""
from __future__ import annotations

import copy
from typing import Union, Optional

import lightkurve
import numpy as np

import pandas as pd
from astropy import units
from astropy.coordinates import SkyCoord, Angle
from astroquery.mast import Catalogs
from lightkurve.targetpixelfile import TargetPixelFile
from retrying import retry

from ramjet.data_interface.tess_data_interface import is_common_mast_connection_error
from ramjet.photometric_database.light_curve import LightCurve


class TessLightCurve(LightCurve):
    """
    A class to represent a TESS light curve.
    """
    def __init__(self):
        super().__init__()
        self.tic_id: Union[int, None] = None
        self.sector: Union[int, None] = None
        self._tic_row: Optional[pd.Series] = None

    @property
    @retry(retry_on_exception=is_common_mast_connection_error)
    def sky_coord(self) -> SkyCoord:
        tic_row = self.get_tic_row()
        sky_coord = SkyCoord(ra=tic_row['ra'], dec=tic_row['dec'], unit=units.deg)
        return sky_coord

    def get_tic_row(self):
        if self._tic_row is None:
            self._tic_row = Catalogs.query_object(f'TIC{self.tic_id}', catalog='TIC').to_pandas().iloc[0]
        return self._tic_row

    @property
    @retry(retry_on_exception=is_common_mast_connection_error)
    def tess_magnitude(self) -> float:
        tic_row = self.get_tic_row()
        return float(tic_row['Tmag'])

    @retry(retry_on_exception=is_common_mast_connection_error)
    def get_ffi_time_series_from_tess_cut(self) -> TargetPixelFile:
        search_result = lightkurve.search_tesscut(f'TIC{self.tic_id}', sector=self.sector)
        target_pixel_file = search_result.download(cutout_size=10)
        return target_pixel_file

    def get_photometric_centroid_of_variability(self, minimum_period: Optional[float] = None,
                                                maximum_period: Optional[float] = None) -> SkyCoord:
        fold_period, fold_epoch, time_bin_size, minimum_bin_phase, maximum_bin_phase = \
            self.get_variability_phase_folding_parameters(minimum_period=minimum_period, maximum_period=maximum_period)
        variability_centroid_and_frames = \
            self.get_photometric_variability_centroid_and_frames_from_folding_parameters(fold_epoch,
                                                                                         fold_period,
                                                                                         maximum_bin_phase,
                                                                                         minimum_bin_phase,
                                                                                         time_bin_size)
        centroid_sky_coord = variability_centroid_and_frames[0]
        return centroid_sky_coord

    def get_photometric_variability_centroid_and_frames_from_folding_parameters(
            self, fold_epoch: float, fold_period: float, maximum_bin_phase: float, minimum_bin_phase: float,
            time_bin_size: float):
        target_pixel_file = self.get_ffi_time_series_from_tess_cut()
        if target_pixel_file is None:
            raise CentroidAlgorithmFailedError
        phases = ((target_pixel_file.time.value - fold_epoch) % fold_period)
        minimum_bin_indexes = np.where((phases > (minimum_bin_phase - time_bin_size)) &
                                       (phases < (minimum_bin_phase + time_bin_size)))
        maximum_bin_indexes = np.where((phases > (maximum_bin_phase - time_bin_size)) &
                                       (phases < (maximum_bin_phase + time_bin_size)))
        # Hack to get a single frame target pixel file with the right coordinates, etc. by copying the first frame.
        median_minimum_target_pixel_frame = copy.deepcopy(target_pixel_file[minimum_bin_indexes][0])
        median_minimum_target_pixel_frame.hdu[1].data["FLUX"] = np.nanmedian(
            target_pixel_file[minimum_bin_indexes].flux.value, axis=0, keepdims=True)
        # Hack to get a single frame target pixel file with the right coordinates, etc. by copying the first frame.
        median_maximum_target_pixel_frame = copy.deepcopy(target_pixel_file[maximum_bin_indexes][0])
        median_maximum_target_pixel_frame.hdu[1].data["FLUX"] = np.nanmedian(
            target_pixel_file[maximum_bin_indexes].flux.value, axis=0, keepdims=True)
        # Hack to get a single frame target pixel file with the right coordinates, etc. by copying the first frame.
        difference_target_pixel_frame = copy.deepcopy(target_pixel_file[0])
        difference_flux_frame = median_maximum_target_pixel_frame.flux.value - median_minimum_target_pixel_frame.flux.value
        difference_target_pixel_frame.hdu[1].data["FLUX"] = difference_flux_frame
        image_side_size = 10
        pixel_side_indexes = np.arange(image_side_size, dtype=np.float32)
        flux_difference = difference_flux_frame[0]
        positive_flux_difference = np.maximum(flux_difference, 0)
        try:
            x_flux_difference_centroid = np.average(pixel_side_indexes,
                                                    weights=np.mean(positive_flux_difference, axis=0))
            y_flux_difference_centroid = np.average(pixel_side_indexes,
                                                    weights=np.mean(positive_flux_difference, axis=1))
            centroid_sky_coord = target_pixel_file.wcs.pixel_to_world(x_flux_difference_centroid,
                                                                      y_flux_difference_centroid)
        except ZeroDivisionError as error:
            raise CentroidAlgorithmFailedError from error
        return centroid_sky_coord, target_pixel_file, difference_target_pixel_frame, median_maximum_target_pixel_frame, median_minimum_target_pixel_frame

    def get_angular_distance_to_variability_photometric_centroid(self, minimum_period: Optional[float] = None,
                                                                 maximum_period: Optional[float] = None) -> Angle:
        centroid_sky_coord = self.get_photometric_centroid_of_variability(minimum_period=minimum_period,
                                                                          maximum_period=maximum_period)
        return self.sky_coord.separation(centroid_sky_coord)


class CentroidAlgorithmFailedError(Exception):
    pass
