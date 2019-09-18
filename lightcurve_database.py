"""Code for representing a dataset of lightcurves for binary classification."""
import math
import os
import random
from typing import List

import numpy as np
import pandas as pd
import tensorflow as tf


class LightcurveDatabase:
    """A representing a dataset of lightcurves for binary classification."""

    def __init__(self):
        self.time_steps_per_example = 30000
        self.batch_size = 100
        self.trial_directory = None

    def generate_datasets(self, positive_data_directory, negative_data_directory,
                          positive_to_negative_data_ratio: float = None) -> (tf.data.Dataset, tf.data.Dataset):
        """Generates the training and validation datasets."""
        data_format_suffixes = ('.npy', '.pkl', '.feather')
        positive_example_paths = [os.path.join(positive_data_directory, file_name) for file_name in
                                  os.listdir(positive_data_directory) if file_name.endswith(data_format_suffixes)]
        print(f'{len(positive_example_paths)} positive examples.')
        negative_example_paths = [os.path.join(negative_data_directory, file_name) for file_name in
                                  os.listdir(negative_data_directory) if file_name.endswith(data_format_suffixes)]
        print(f'{len(negative_example_paths)} negative examples.')
        positive_datasets = self.get_training_and_validation_datasets_for_file_paths(positive_example_paths, 1)
        positive_training_dataset, positive_validation_dataset = positive_datasets
        negative_datasets = self.get_training_and_validation_datasets_for_file_paths(negative_example_paths, 0)
        negative_training_dataset, negative_validation_dataset = negative_datasets
        training_dataset = self.get_ratio_enforced_dataset(positive_training_dataset, negative_training_dataset,
                                                           positive_to_negative_data_ratio)
        validation_dataset = positive_validation_dataset.concatenate(negative_validation_dataset)
        if self.trial_directory is not None:
            self.log_dataset_file_names(training_dataset, dataset_name='training')
            self.log_dataset_file_names(validation_dataset, dataset_name='validation')
        load_and_preprocess_function = lambda file_path, label: tuple(
            tf.py_function(self.load_and_preprocess_example_file, [file_path, label], [tf.float32, tf.int32]))
        training_dataset = training_dataset.shuffle(buffer_size=len(list(training_dataset)))
        training_dataset = training_dataset.map(load_and_preprocess_function, num_parallel_calls=16)
        training_dataset = training_dataset.map(self.set_shape_function, num_parallel_calls=16)
        training_dataset = training_dataset.batch(self.batch_size).prefetch(buffer_size=tf.data.experimental.AUTOTUNE)
        validation_dataset = validation_dataset.map(load_and_preprocess_function, num_parallel_calls=16)
        validation_dataset = validation_dataset.map(self.set_shape_function, num_parallel_calls=16)
        validation_dataset = validation_dataset.batch(self.batch_size).prefetch(
            buffer_size=tf.data.experimental.AUTOTUNE)
        return training_dataset, validation_dataset

    def log_dataset_file_names(self, dataset: tf.data.Dataset, dataset_name: str):
        """Saves the names of the files used in a dataset to a CSV file in the trail directory."""
        os.makedirs(self.trial_directory, exist_ok=True)
        training_example_paths = [example[0].numpy().decode('utf-8') for example in list(dataset)]
        series = pd.Series(training_example_paths)
        series.to_csv(os.path.join(self.trial_directory, f'{dataset_name}.csv'), header=False, index=False)

    def set_shape_function(self, lightcurve: tf.Tensor, label: tf.Tensor):
        """
        Explicitly sets the shapes of the lightcurve and label tensor, otherwise TensorFlow can't infer it.

        :param lightcurve: The lightcurve tensor.
        :param label: The label tensor.
        :return: The lightcurve and label tensor with TensorFlow inferable shapes.
        """
        lightcurve.set_shape([self.time_steps_per_example, 1])
        label.set_shape([1])
        return lightcurve, label

    def get_ratio_enforced_dataset(self, positive_training_dataset: tf.data.Dataset,
                                   negative_training_dataset: tf.data.Dataset,
                                   positive_to_negative_data_ratio: float) -> tf.data.Dataset:
        """Generates a dataset with an enforced data ratio."""
        if positive_to_negative_data_ratio is not None:
            positive_count = len(list(positive_training_dataset))
            negative_count = len(list(negative_training_dataset))
            existing_ratio = positive_count / negative_count
            if existing_ratio < positive_to_negative_data_ratio:
                desired_number_of_positive_examples = int(positive_to_negative_data_ratio * negative_count)
                positive_training_dataset = self.repeat_dataset_to_size(positive_training_dataset,
                                                                        desired_number_of_positive_examples)
            else:
                desired_number_of_negative_examples = int((1 / positive_to_negative_data_ratio) * positive_count)
                negative_training_dataset = self.repeat_dataset_to_size(negative_training_dataset,
                                                                        desired_number_of_negative_examples)
        return positive_training_dataset.concatenate(negative_training_dataset)

    @staticmethod
    def repeat_dataset_to_size(dataset: tf.data.Dataset, size: int) -> tf.data.Dataset:
        """Repeats a dataset to make it a desired length."""
        current_size = len(list(dataset))
        times_to_repeat = math.ceil(size / current_size)
        return dataset.repeat(times_to_repeat).take(size)

    def get_training_and_validation_datasets_for_file_paths(self, example_paths: List[str], label: int,
                                                            validation_dataset_size_ratio: float = 0.2) -> (
                                                            tf.data.Dataset, tf.data.Dataset):
        """Creates a TensorFlow Dataset from a list of file names and desired label for those files."""
        labels = [label] * len(example_paths)
        example_paths, labels = self.shuffle_in_unison(example_paths, labels, seed=0)
        labels = labels.astype(np.int32)
        labels = np.expand_dims(labels, axis=-1)
        file_path_dataset = tf.data.Dataset.from_tensor_slices(example_paths)
        labels_dataset = tf.data.Dataset.from_tensor_slices(labels)
        dataset = tf.data.Dataset.zip((file_path_dataset, labels_dataset))
        validation_dataset_size = int(len(labels) * validation_dataset_size_ratio)
        validation_dataset = dataset.take(validation_dataset_size)
        training_dataset = dataset.skip(validation_dataset_size)
        return training_dataset, validation_dataset

    def load_and_preprocess_example_file(self, file_path: tf.Tensor, label: int = None) -> (np.ndarray, int):
        """Loads numpy files from the tensor alongside labels."""
        file_path_string = file_path.numpy().decode('utf-8')
        if file_path_string.endswith('.npy'):
            lightcurve = np.load(file_path_string)
        elif file_path_string.endswith('.pkl'):
            lightcurve = pd.read_pickle(file_path_string)['flux'].values
        elif file_path_string.endswith('.feather'):
            lightcurve = pd.read_feather(file_path_string)['flux'].values
        else:
            raise ValueError(f'Unknown extension when loading data from {file_path}')
        lightcurve = self.preprocess_and_augment_lightcurve(lightcurve)
        if label is None:
            return lightcurve.astype(np.float32)
        else:
            return lightcurve.astype(np.float32), label

    def preprocess_and_augment_lightcurve(self, lightcurve: np.ndarray) -> np.ndarray:
        """Prepares the lightcurves for training with several preprocessing and augmenting steps."""
        lightcurve = self.remove_random_values(lightcurve)  # Helps prevent overfitting.
        lightcurve = self.roll_lightcurve(lightcurve)  # Helps prevent overfitting.
        lightcurve = self.make_uniform_length(lightcurve)  # Current network expects a fixed length.
        lightcurve = self.normalize(lightcurve)
        lightcurve = np.expand_dims(lightcurve, axis=-1)  # Network uses a "channel" dimension.
        return lightcurve

    def make_uniform_length(self, lightcurve: np.ndarray) -> np.ndarray:
        """Makes all lightcurves the same length, by clipping those too large and repeating those too small."""
        if lightcurve.shape[0] > self.time_steps_per_example:
            start_slice = np.random.randint(0, lightcurve.shape[0] - self.time_steps_per_example)
            lightcurve = lightcurve[start_slice:start_slice + self.time_steps_per_example]
        else:
            elements_to_repeat = self.time_steps_per_example - lightcurve.shape[0]
            lightcurve = np.pad(lightcurve, (0, elements_to_repeat), mode='wrap')
        return lightcurve

    @staticmethod
    def normalize(lightcurve: np.ndarray) -> np.ndarray:
        """Normalizes from 0 to 1 on the logarithm of the lightcurve."""
        lightcurve -= np.min(lightcurve)
        lightcurve = np.log1p(lightcurve)
        array_max = np.max(lightcurve)
        if array_max != 0:
            lightcurve /= array_max
        return lightcurve

    @staticmethod
    def remove_bad_files(file_path_list: List[str]) -> List[str]:
        """Removes problematic lightcurves (all values the same, containing infinite or NaN values)."""
        new_file_path_list = []
        for file_path in file_path_list:
            array = np.load(file_path)
            if np.max(array) == np.min(array):
                continue
            if not np.isfinite(array).all():
                continue
            if np.isnan(array).any():
                continue
            new_file_path_list.append(file_path)
        print(f'{len(file_path_list) - len(new_file_path_list)} items with bad values removed.')
        return new_file_path_list

    @staticmethod
    def shuffle_in_unison(a, b, seed=None):
        """Shuffle two arrays in unison."""
        if seed is not None:
            np.random.seed(seed)
        indexes = np.random.permutation(len(a))
        return np.array(a)[indexes], np.array(b)[indexes]

    @staticmethod
    def remove_random_values(lightcurve: np.ndarray) -> np.ndarray:
        """Removes random values from the lightcurve."""
        max_values_to_remove = 10
        values_to_remove = random.randrange(max_values_to_remove)
        random_indexes = np.random.randint(0, len(lightcurve), size=values_to_remove)
        return np.delete(lightcurve, random_indexes)

    @staticmethod
    def roll_lightcurve(lightcurve: np.ndarray) -> np.ndarray:
        """Randomly rolls the lightcurve, moving starting elements to the end."""
        shift = np.random.randint(0, len(lightcurve))
        return np.roll(lightcurve, shift)

    def generate_inference_dataset(self, inference_directory: str) -> (List[str], List[np.ndarray]):
        """Generates the inference dataset."""
        example_paths = [os.path.join(inference_directory, file_name) for file_name in
                         os.listdir(inference_directory) if file_name.endswith('.npy')]
        examples = []
        for example_path in example_paths:
            lightcurve = np.load(example_path)
            lightcurve = self.preprocess_and_augment_lightcurve(lightcurve)
            examples.append(lightcurve.astype(np.float32))
        return example_paths, examples
