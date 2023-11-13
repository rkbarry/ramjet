from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import List

import numpy as np
import stringcase
import torch
import wandb
from torch.nn import BCELoss, Module
from torch.optim import AdamW
from torch.utils.data import DataLoader
from torchmetrics.classification import BinaryAccuracy

from qusi.light_curve_dataset import LightCurveDataset, InterleavedDataset
from qusi.wandb_liaison import wandb_init, wandb_log, wandb_commit


@dataclass
class TrainSession:
    train_datasets: List[LightCurveDataset]
    validation_datasets: List[LightCurveDataset]
    model: Module
    batch_size: int
    cycles: int
    train_steps_per_cycle: int
    validation_steps_per_cycle: int

    @classmethod
    def new(cls, train_datasets: List[LightCurveDataset],
            validation_datasets: List[LightCurveDataset], model: Module, batch_size: int,
            cycles: int, train_steps_per_cycle: int, validation_steps_per_cycle: int):
        instance = cls(train_datasets=train_datasets,
                       validation_datasets=validation_datasets,
                       model=model,
                       batch_size=batch_size,
                       cycles=cycles,
                       train_steps_per_cycle=train_steps_per_cycle,
                       validation_steps_per_cycle=validation_steps_per_cycle)
        return instance

    def run(self):
        wandb_init(process_rank=0, project='qusi', entity='ramjet',
                   settings=wandb.Settings(start_method='fork'))
        sessions_directory = Path('sessions')
        sessions_directory.mkdir(exist_ok=True)
        train_dataset = InterleavedDataset.new(*self.train_datasets)
        torch.multiprocessing.set_start_method('spawn')
        debug = False
        if debug:
            workers_per_dataloader = 0
            prefetch_factor = None
            persistent_workers = False
        else:
            workers_per_dataloader = 10
            prefetch_factor = 10
            persistent_workers = True
        train_dataloader = DataLoader(train_dataset, batch_size=self.batch_size, pin_memory=True,
                                      persistent_workers=persistent_workers, prefetch_factor=prefetch_factor,
                                      num_workers=workers_per_dataloader)
        validation_dataloaders: List[DataLoader] = []
        for validation_dataset in self.validation_datasets:
            validation_dataloaders.append(DataLoader(validation_dataset, batch_size=self.batch_size, pin_memory=True,
                                                     persistent_workers=persistent_workers,
                                                     prefetch_factor=prefetch_factor,
                                                     num_workers=workers_per_dataloader))
        if torch.cuda.is_available() and not debug:
            device = torch.device('cuda')
        elif torch.backends.mps.is_available() and not debug:
            device = torch.device("mps")
        else:
            device = torch.device('cpu')
        self.model = self.model.to(device, non_blocking=True)
        loss_function = BCELoss().to(device, non_blocking=True)
        metric_functions = [BinaryAccuracy()]
        optimizer = AdamW(self.model.parameters())
        metric_functions_on_device: List[Module] = []
        for metric_function in metric_functions:
            metric_functions_on_device.append(metric_function.to(device, non_blocking=True))
        metric_functions = metric_functions_on_device
        for cycle_index in range(self.cycles):
            train_phase(dataloader=train_dataloader, model=self.model, loss_function=loss_function,
                        metric_functions=metric_functions, optimizer=optimizer,
                        steps=self.train_steps_per_cycle, device=device)
            for validation_dataloader in validation_dataloaders:
                validation_phase(dataloader=validation_dataloader, model=self.model, loss_function=loss_function,
                                 metric_functions=metric_functions, steps=self.validation_steps_per_cycle,
                                 device=device)
            save_model(self.model, suffix='latest_model', process_rank=0)
            wandb_commit(process_rank=0)


def train_phase(dataloader, model, loss_function, metric_functions: List[Module], optimizer, steps, device):
    model.train()
    total_loss = 0
    metric_totals = np.zeros(shape=[len(metric_functions)])
    for batch_index, (input_features, targets) in enumerate(dataloader):
        # Compute prediction and loss
        # TODO: The conversion to float32 probably shouldn't be here, but the default collate_fn seems to be converting
        #  to float64. Probably should override the default collate.
        targets = targets.to(torch.float32).to(device, non_blocking=True)
        input_features = input_features.to(torch.float32).to(device, non_blocking=True)
        predicted_targets = model(input_features)
        loss = loss_function(predicted_targets, targets)

        # Backpropagation
        optimizer.zero_grad()
        loss.backward()
        optimizer.step()

        loss, current = loss.to(device, non_blocking=True).item(), (batch_index + 1) * len(input_features)
        total_loss += loss
        for metric_function_index, metric_function in enumerate(metric_functions):
            batch_metric_value = metric_function(predicted_targets.to(device, non_blocking=True),
                                                 targets).item()
            metric_totals[metric_function_index] += batch_metric_value
        if batch_index % 10 == 0:
            print(f"loss: {loss:>7f}  [{current:>5d}/{steps * len(input_features):>5d}]", flush=True)
        if batch_index + 1 >= steps:
            break
    wandb_log('loss', total_loss / steps, process_rank=0)
    cycle_metric_values = metric_totals / steps
    for metric_function_index, metric_function in enumerate(metric_functions):
        wandb_log(f'{get_metric_name(metric_function)}', cycle_metric_values[metric_function_index],
                  process_rank=0)


def get_metric_name(metric_function):
    metric_name = type(metric_function).__name__
    metric_name = stringcase.snakecase(metric_name)
    metric_name = metric_name.replace('_metric', '').replace('_loss', '')
    return metric_name


def validation_phase(dataloader, model, loss_function, metric_functions: List[Module], steps, device):
    model.eval()
    validation_loss = 0
    metric_totals = np.zeros(shape=[len(metric_functions)])

    with torch.no_grad():
        for batch, (input_features, targets) in enumerate(dataloader):
            targets = targets.to(torch.float32).to(device, non_blocking=True)
            input_features = input_features.to(torch.float32).to(device, non_blocking=True)
            predicted_targets = model(input_features)
            validation_loss += loss_function(predicted_targets, targets).to(device, non_blocking=True).item()
            for metric_function_index, metric_function in enumerate(metric_functions):
                batch_metric_value = metric_function(predicted_targets.to(device, non_blocking=True),
                                                     targets).item()
                metric_totals[metric_function_index] += batch_metric_value
            if batch + 1 >= steps:
                break

    validation_loss /= steps
    print(f"Validation Error: \nAvg loss: {validation_loss:>8f} \n")
    wandb_log('val_loss', validation_loss, process_rank=0)
    cycle_metric_values = metric_totals / steps
    for metric_function_index, metric_function in enumerate(metric_functions):
        wandb_log(f'val_{get_metric_name(metric_function)}', cycle_metric_values[metric_function_index],
                  process_rank=0)


def save_model(model: Module, suffix: str, process_rank: int):
    if process_rank == 0:
        model_name = wandb.run.name
        if model_name == '':
            model_name = wandb.run.id
        torch.save(model.state_dict(), Path(f'sessions/{model_name}_{suffix}.pt'))