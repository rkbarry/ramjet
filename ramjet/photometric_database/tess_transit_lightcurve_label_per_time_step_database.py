"""
Code for a database of TESS transit lightcurves with a label per time step.
"""
import shutil
from pathlib import Path
from typing import List, Union

import numpy as np
import pandas as pd
import tensorflow as tf
from astropy.io import fits
from astropy.table import Table
from astroquery.mast import Observations
from astroquery.exceptions import TimeoutError as AstroQueryTimeoutError
from requests.exceptions import ConnectionError

from ramjet.photometric_database.lightcurve_label_per_time_step_database import LightcurveLabelPerTimeStepDatabase
from ramjet.photometric_database.tess_data_interface import TessDataInterface


class TessTransitLightcurveLabelPerTimeStepDatabase(LightcurveLabelPerTimeStepDatabase):
    """
    A class for a database of TESS transit lightcurves with a label per time step.
    """

    def __init__(self, data_directory='data/tess'):
        super().__init__(data_directory=data_directory)
        self.meta_data_frame: Union[pd.DataFrame, None] = None
        self.lightcurve_directory = self.data_directory.joinpath('lightcurves')
        self.data_validation_directory = self.data_directory.joinpath('data_validations')
        self.data_validation_dictionary = None
        Observations.TIMEOUT = 1200  # Set Astroquery API limits to give less connection errors.
        Observations.PAGESIZE = 10000

    def create_data_directories(self):
        """
        Creates the data directories to be used by the database.
        """
        self.data_directory.mkdir(parents=True, exist_ok=True)
        self.lightcurve_directory.mkdir(parents=True, exist_ok=True)
        self.data_validation_directory.mkdir(parents=True, exist_ok=True)

    def clear_data_directory(self):
        """
        Empties the data directory.
        """
        if self.data_directory.exists():
            shutil.rmtree(self.data_directory)
        self.create_data_directories()

    def get_lightcurve_file_paths(self) -> List[Path]:
        """
        Gets all the file paths for the available lightcurves.
        """
        return list(self.lightcurve_directory.glob('*.fits'))

    def is_positive(self, example_path):
        """
        Checks if an example contains a transit event or not.

        :param example_path: The path to the example to check.
        :return: Whether or not the example contains a transit event.
        """
        return example_path in self.meta_data_frame['lightcurve_path'].values

    @staticmethod
    def add_sector_column_based_on_single_sector_obs_id(observations: pd.DataFrame) -> pd.DataFrame:
        """
        Adds a column with the sector the data was taken from.

        :param observations: The table of single-sector observations.
        :return: The table with the added sector column.
        """
        tess_data_interface = TessDataInterface()
        observations['sector'] = observations['obs_id'].map(tess_data_interface.get_sector_from_single_sector_obs_id)
        return observations

    def add_sector_columns_based_on_multi_sector_obs_id(self, observations: pd.DataFrame) -> pd.DataFrame:
        """
        Adds columns with sector information the data was taken from. In particular, adds the start and end
        sectors, as well as the total length of the sector range.

        :param observations: The data frame of multi-sector observations.
        :return: The data frame with the added sector information columns.
        """
        sectors_data_frame = observations['obs_id'].apply(self.get_sectors_from_multi_sector_obs_id)
        observations['start_sector'] = sectors_data_frame[0]
        observations['end_sector'] = sectors_data_frame[1]
        observations['sector_range_length'] = observations['end_sector'] - observations['start_sector'] + 1
        return observations

    @staticmethod
    def get_sectors_from_multi_sector_obs_id(obs_id: str) -> pd.Series:
        """
        Extracts the sectors from a multi-sector obs_id string.

        :param obs_id: The obs_id to extract from.
        :return: The extracted sector numbers: a start and an end sector.
        """
        string_split = obs_id.split('-')
        return pd.Series([int(string_split[1][1:]), int(string_split[2][1:])])

    @staticmethod
    def get_largest_sector_range(multi_sector_observations: pd.DataFrame) -> pd.DataFrame:
        """
        Returns only the rows with the largest sector range for each TIC ID.

        :param multi_sector_observations: The observations with sector range information included.
        :return: A data frame containing only the rows for each TIC ID that have the largest sector range.
        """
        range_sorted_observations = multi_sector_observations.sort_values('sector_range_length', ascending=False)
        return range_sorted_observations.drop_duplicates(['target_name'])

    @staticmethod
    def load_fluxes_and_times_from_fits_file(example_path: Union[str, Path]) -> (np.ndarray, np.ndarray):
        """
        Extract the flux and time values from a TESS FITS file.

        :param example_path: The path to the FITS file.
        :return: The flux and times values from the FITS file.
        """
        hdu_list = fits.open(example_path)
        lightcurve = hdu_list[1].data  # Lightcurve information is in first extension table.
        fluxes = lightcurve['SAP_FLUX']
        times = lightcurve['TIME']
        assert times.shape == fluxes.shape
        # noinspection PyUnresolvedReferences
        nan_indexes = np.union1d(np.argwhere(np.isnan(fluxes)), np.argwhere(np.isnan(times)))
        fluxes = np.delete(fluxes, nan_indexes)
        times = np.delete(times, nan_indexes)
        return fluxes, times

    def generate_label(self, example_path: str, times: np.float32) -> np.bool:
        """
        Generates a label for each time step defining whether or not a transit is occurring.

        :param example_path: The path of the lightcurve file (to determine which row of the meta data to use).
        :param times: The times of the measurements in the lightcurve.
        :return: A boolean label for each time step specifying if transiting is occurring at that time step.
        """
        any_planet_is_transiting = np.zeros_like(times, dtype=np.bool)
        with np.errstate(all='raise'):
            try:
                planets_meta_data = self.meta_data_frame[self.meta_data_frame['lightcurve_path'] == example_path]
                for index, planet_meta_data in planets_meta_data.iterrows():
                    transit_tess_epoch = planet_meta_data['transit_epoch'] - 2457000  # Offset of BJD to BTJD
                    epoch_times = times - transit_tess_epoch
                    transit_duration = planet_meta_data['transit_duration'] / 24  # Convert from hours to days.
                    transit_period = planet_meta_data['transit_period']
                    half_duration = transit_duration / 2
                    if transit_period == 0:  # Single transit known, no repeating signal.
                        planet_is_transiting = (-half_duration < epoch_times) & (epoch_times < half_duration)
                    else:  # Period known, signal should repeat every period.
                        planet_is_transiting = ((epoch_times + half_duration) % transit_period) < transit_duration
                    any_planet_is_transiting = any_planet_is_transiting | planet_is_transiting
            except FloatingPointError as error:
                print(example_path)
                raise error
        return any_planet_is_transiting

    def general_preprocessing(self, example_path_tensor: tf.Tensor) -> (tf.Tensor, tf.Tensor):
        """
        Loads and preprocesses the data.

        :param example_path_tensor: The tensor containing the path to the example to load.
        :return: The example and its corresponding label.
        """
        example_path = example_path_tensor.numpy().decode('utf-8')
        fluxes, times = self.load_fluxes_and_times_from_fits_file(example_path)
        fluxes = self.normalize(fluxes)
        time_differences = np.diff(times, prepend=times[0])
        example = np.stack([fluxes, time_differences], axis=-1)
        if self.is_positive(example_path):
            label = self.generate_label(example_path, times)
        else:
            label = np.zeros_like(fluxes)
        return tf.convert_to_tensor(example, dtype=tf.float32), tf.convert_to_tensor(label, dtype=tf.float32)
