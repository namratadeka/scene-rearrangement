from tqdm import tqdm
from collections import defaultdict, OrderedDict

import wandb
import torch
import logging
import numpy as np
import torch.optim as optim
from os.path import join, exists
from torch.utils.data import DataLoader

from data import data
from model import model
from losses.losses import *
from visualization import wandb_utils
from utils.utils import dict_to_device, detach_2_np, copy_state_dict


class VAETrainer(object):
    def __init__(self, data_cfg, model_cfg, exp_cfg):
        self.data_cfg = data_cfg
        self.model_cfg = model_cfg
        self.exp_cfg = exp_cfg

        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        self._setup_dataloaders()
        self.model = model.factory.create(self.model_cfg.model_key, **{"model_cfg": self.model_cfg}).to(self.device)
        if self.exp_cfg.wandb:
            wandb.watch(self.model)
        self._setup_optimizers()

        if self.exp_cfg.load:
            self.load_checkpoint(self.exp_cfg.load)

        self.current_total_loss = np.inf

    def _setup_dataloaders(self):
        self.datasets = defaultdict(list)
        for mode in self.model_cfg.modes:
            for source in self.data_cfg.sources:
                for key, args in source.items():
                    args.update({"data_dir": args[mode]})
                    self.datasets[mode].append((data.factory.create(key, **args), args["batch_size"], key))

        self.dataloaders = self.create_dataloaders()

    def create_dataloaders(self):
        dataloaders = defaultdict(OrderedDict)

        for mode in self.datasets.keys():

            shuffle = mode == "train"
            drop_last = mode == "train"

            for dataset in self.datasets[mode]:
                dataloader = DataLoader(
                    dataset=dataset[0],
                    batch_size=dataset[1],
                    shuffle=shuffle,
                    drop_last=drop_last,
                    pin_memory=True,
                    num_workers=self.data_cfg.num_workers,
                )

                dataloaders[mode][dataset[2]] = dataloader

        return dataloaders

    def _setup_optimizers(self):
        vae_params = list(self.model.parameters())
        vae_optim_cfg = self.model_cfg.optimizers["vae"]

        self.vae_opt =  eval(
            "optim.{}(vae_params, **{})".format([*vae_optim_cfg.keys()][0], [*vae_optim_cfg.values()][0])
        )

    def save_checkpoint(self, epochID:int):
        """Saves trained checkpoint (model and optimizer)
        Args:
            epochID (int): Epoch number of saved checkpoint
            save_path (str): Absolute path of where to save the checkpoint
        """
        save_dict = {
            "state_dict": self.model.state_dict(),
            "optimizer": self.vae_opt.state_dict(),
            "epoch": epochID
        }
        fname = "{}_{}.pth".format(self.model_cfg.model_key, epochID)
        torch.save(save_dict, join(self.exp_cfg.CKPT_DIR, fname))
        logging.info("Saved checkpoint {}.".format(epochID))

    def load_checkpoint(self, load_path: str):
        """Load pre-trained checkpoint (model and optimizer)
        Args:
            load_path (str): Absolute path of where to load checkpoints from
        """
        if exists(load_path):
            checkpoint = torch.load(load_path)
            logging.info("Loading model weights from {}.".format(load_path))
            copy_state_dict(self.model.state_dict(), checkpoint["state_dict"])
            if self.exp_cfg.init_opt:
                self.vae_opt.load_state_dict(checkpoint["optimizer"])
        else:
            logging.error("Checkpoint {} not found.".format(load_path))

    def compare_and_save(self, loss:float, epochID:int):
        if loss < self.current_total_loss:
            self.save_checkpoint(epochID)
            self.current_total_loss = loss

    def _backprop(self, loss):
        self.vae_opt.zero_grad()
        loss.backward()
        self.vae_opt.step()

    def _aggregate_losses(self, losses):
        for key in losses:
            losses[key] = np.mean(losses[key])
        return losses

    def _log_epoch_summary(self, epochID, mode, losses):
        if self.exp_cfg.wandb:
            wandb_utils.log_epoch_summary(epochID, mode, losses)

    def _epoch(self, mode:str, epochID: int):
        if mode.__eq__('train'):
            self.model.train()
        else:
            self.model.eval()
        data_iter = iter(self.dataloaders[mode]["kitti360_semantic_1hot"])      # TODO fix the way Kitti360Semantic1Hot is used
        iterator = tqdm(range(len(self.dataloaders[mode]["kitti360_semantic_1hot"])), dynamic_ncols=True)

        losses = defaultdict(list)

        for i in iterator:
            batch_data = dict_to_device(next(data_iter), self.device)

            # for j in tqdm(range(10), desc="inner iterator"):
            if mode.__eq__('train'):
                model_out = self.model(batch_data["mask_out"].unsqueeze(1))
            else:
                with torch.no_grad():
                    model_out = self.model(batch_data["mask_out"].unsqueeze(1))

            reconst_loss = eval(self.model_cfg.reconstruction_loss)(model_out.reconst, batch_data['mask_out'], self.model_cfg.loss_weights['reconstruction'])
            kld = KL(model_out.mu, model_out.log_var)

            loss = reconst_loss + self.model_cfg.loss_weights['kld'] * kld

            if mode.__eq__('train'):
                self._backprop(loss)

            iterator.set_description("V: {} | Epoch: {} | {} | Loss: {:.4f}".format(self.exp_cfg.version,
                epochID, mode, loss.item()), refresh=True)

            losses['total_loss'].append(loss.item())
            losses['reconstruction_loss'].append(reconst_loss.item())
            losses['KL-divergence'].append(kld.item())

            # visualize images from the last batch
            if self.exp_cfg.wandb and i == len(iterator) - 1:
                viz_gt = detach_2_np(batch_data['mask_out'].unsqueeze(1))
                viz_pred = detach_2_np(torch.nn.Softmax(dim=1)(model_out.reconst))

        losses = self._aggregate_losses(losses)
        self._log_epoch_summary(epochID, mode, losses)
        if self.exp_cfg.wandb:
            wandb_utils.visualize_images(epochID, mode, viz_gt, viz_pred)

    def train(self):
        for epochID in range(self.model_cfg.epochs):
            for mode in self.model_cfg.modes:
                self._epoch(mode, epochID)


class VAETrainerBuilder(object):
    """VAE Trainer Builder Class
    """

    def __init__(self):
        """VAE Trainer Builder Class Constructor
        """
        self._instance = None

    def __call__(self, data_cfg, model_cfg, exp_cfg, **_ignored):
        """Callback function
        Args:
            data_cfg (Config): Data Config object
            model_cfg (Config): Model Config object
            exp_cfg (Config): Experiment Config object
        Returns:
            VAETrainer: Instantiated VAE trainer object
        """
        if not self._instance:
            self._instance = VAETrainer(data_cfg=data_cfg, model_cfg=model_cfg, exp_cfg=exp_cfg)
        return self._instance
