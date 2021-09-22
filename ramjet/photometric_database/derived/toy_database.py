import numpy as np

from ramjet.photometric_database.derived.toy_light_curve_collection import ToyFlatLightCurveCollection, \
    ToySineWaveLightCurveCollection
from ramjet.photometric_database.standard_and_injected_light_curve_database import StandardAndInjectedLightCurveDatabase


class ToyDatabase(StandardAndInjectedLightCurveDatabase):
    def __init__(self):
        super().__init__()
        self.batch_size = 10
        self.number_of_parallel_processes_per_map = 1
        self.time_steps_per_example = 100
        self.training_standard_light_curve_collections = [
            ToyFlatLightCurveCollection(),
            ToySineWaveLightCurveCollection(),
        ]
        self.validation_standard_light_curve_collections = [
            ToyFlatLightCurveCollection(),
            ToySineWaveLightCurveCollection(),
        ]
        self.inference_light_curve_collections = [
            ToyFlatLightCurveCollection(),
            ToySineWaveLightCurveCollection(),
        ]

class ToyDatabaseWithAuxiliary(StandardAndInjectedLightCurveDatabase):
    def __init__(self):
        super().__init__()
        self.batch_size = 10
        self.number_of_parallel_processes_per_map = 1
        self.time_steps_per_example = 100
        self.number_of_auxiliary_values = 2
        flat_collection = ToyFlatLightCurveCollection()
        sine_wave_collection = ToySineWaveLightCurveCollection()
        flat_collection.load_auxiliary_information_for_path = lambda path: np.array([0, 0], dtype=np.float32)
        sine_wave_collection.load_auxiliary_information_for_path = lambda path: np.array([1, 1], dtype=np.float32)
        self.training_standard_light_curve_collections = [
            flat_collection,
            sine_wave_collection,
        ]
        self.validation_standard_light_curve_collections = [
            flat_collection,
            sine_wave_collection,
        ]
        self.inference_light_curve_collections = [
            flat_collection,
            sine_wave_collection,
        ]