"""Tests for the PyMapper class."""
import os
import time
import pytest
import numpy as np
import tensorflow as tf

from ramjet.photometric_database.py_mapper import PyMapper, map_py_function_to_dataset


class TestPyMapper:
    """Tests for the PyMapper class."""

    @pytest.fixture
    def dataset(self) -> tf.data.Dataset:
        """
        Sets up the dataset for use in a test.

        :return: The dataset.
        """
        return tf.data.Dataset.from_tensor_slices([0, 10, 20, 30])

    @pytest.mark.slow
    def test_py_map_runs_function_on_multiple_processes(self, dataset: tf.data.Dataset):
        py_mapper = PyMapper(sleep_and_get_pid, number_of_parallel_calls=4)
        map_dataset = py_mapper.map_to_dataset(dataset)
        batch_dataset = map_dataset.batch(batch_size=4)
        batch = next(iter(batch_dataset))
        batch_array = batch.numpy()
        unique_pids = set(batch_array)
        assert len(unique_pids) == 4

    def test_py_map_correctly_applies_map_function(self, dataset: tf.data.Dataset):
        py_mapper = PyMapper(add_one, number_of_parallel_calls=4)
        map_dataset = py_mapper.map_to_dataset(dataset)
        batch_dataset = map_dataset.batch(batch_size=4)
        batch = next(iter(batch_dataset))
        batch_array = batch.numpy()
        assert np.array_equal(batch_array, np.array([1, 11, 21, 31]))

    def test_py_map_correctly_applies_map_function_with_two_outputs(self, dataset: tf.data.Dataset):
        py_mapper = PyMapper(add_one_and_add_two, number_of_parallel_calls=4)
        map_dataset = py_mapper.map_to_dataset(dataset, output_types=[tf.float32, tf.float32])
        batch_dataset = map_dataset.batch(batch_size=4)
        batch = next(iter(batch_dataset))
        plus_one_array = batch[0].numpy()
        plus_two_array = batch[1].numpy()
        assert np.array_equal(plus_one_array, np.array([1, 11, 21, 31]))
        assert np.array_equal(plus_two_array, np.array([2, 12, 22, 32]))

    def test_single_function_wrapper(self, dataset):
        mapped_dataset = map_py_function_to_dataset(dataset=dataset, map_function=add_one, number_of_parallel_calls=4,
                                                    output_types=tf.float32)
        batch_dataset = mapped_dataset.batch(batch_size=4)
        batch = next(iter(batch_dataset))
        batch_array = batch.numpy()
        assert np.array_equal(batch_array, np.array([1, 11, 21, 31]))


def sleep_and_get_pid(element) -> int:
    """
    A simple sleep and get pid function to test multiprocessing.

    :return: The pid of the process that ran this function.
    """
    time.sleep(0.1)
    return os.getpid()


def add_one(element: float) -> float:
    """
    Adds 1

    :param element: Input value.
    :return: Input plus 1.
    """
    return element + 1


def add_one_and_add_two(element: float) -> (float, float):
    """
    Adds 1 and adds 2 returning both.

    :param element: Input value.
    :return: Input plus 1 and input plus 2.
    """
    return element + 1, element + 2