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
from utils.utils import dict_to_device, detach_2_np, copy_state_dict, dump_model_output


class ShiftGANTrainer(object):
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
        vae_params = (
                    list(self.model.encoders_pre.parameters())
                    + list(self.model.encoders_post.parameters())
                    + list(self.model.encoders_fc.parameters())
                    + list(self.model.fc_mus.parameters())
                    + list(self.model.fc_vars.parameters())
                    + list(self.model.decoders_fc.parameters())
                    + list(self.model.decoders_pre.parameters())
                    + list(self.model.decoders_post.parameters()))
        vae_optim_cfg = self.model_cfg.optimizers["vae"]

        self.vae_opt =  eval(
            "optim.{}(vae_params, **{})".format([*vae_optim_cfg.keys()][0], [*vae_optim_cfg.values()][0])
        )

        disc_params = list(self.model.discriminator.parameters())
        disc_optim_cfg = self.model_cfg.optimizers["disc"]

        self.disc_opt =  eval(
            "optim.{}(disc_params, **{})".format([*disc_optim_cfg.keys()][0], [*disc_optim_cfg.values()][0])
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

    def _backprop(self, vae_loss, disc_loss):
        self.vae_opt.zero_grad()
        vae_loss.backward()
        self.vae_opt.step()

        self.disc_opt.zero_grad()
        disc_loss.backward()
        self.disc_opt.step()

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
        data_iter = iter(self.dataloaders[mode]["kitti360_semantic_adv"])      # TODO fix the way Kitti360Semantic1Hot is used
        iterator = tqdm(range(len(self.dataloaders[mode]["kitti360_semantic_adv"])), dynamic_ncols=True)

        losses = defaultdict(list)

        for i in iterator:
            batch_data = dict_to_device(next(data_iter), self.device)

            if mode.__eq__('train'):
                model_out = self.model(batch_data["mask_in"])
                disc_real = self.model.disc_forward(batch_data["adv_mask"])
                disc_fake = self.model.disc_forward(self.model.STE(model_out.shifted))
            else:
                with torch.no_grad():
                    model_out = self.model(batch_data["mask_in"])
                    disc_real = self.model.disc_forward(batch_data["adv_mask"])
                    disc_fake = self.model.disc_forward(self.model.STE(model_out.shifted))
                    if self.exp_cfg.dump_output:
                        dump_model_output(model_out.shifted, mode, self.exp_cfg.output_location, batch_data["addr"], flag='shifted')
                        dump_model_output(torch.nn.Softmax(dim=1)(model_out.decoded), mode, self.exp_cfg.output_location, batch_data["addr"])

            reconst_loss = eval(self.model_cfg.reconstruction_loss)(model_out.decoded, batch_data['mask_out'], self.model_cfg.loss_weights['reconstruction'])
            kld = [KL(model_out.mu[vae_stage], model_out.log_var[vae_stage]) for vae_stage in range(len(model_out.mu))] # separate Kld for each VAE
            if self.model_cfg.loss_weights['bin'] > 0:
                bin_loss = binarization_loss(model_out.decoded)
                losses['binarization_loss'].append(bin_loss.item())
            else:
                bin_loss = 0

            shift_hinge = hinge_loss(model_out.shifts)

            vae_adv_loss = eval(self.model_cfg.adv_loss)(disc_fake, mode='real')

            vae_loss = reconst_loss \
                + sum([self.model_cfg.loss_weights['kld'][vae_stage] * kld[vae_stage] for vae_stage in range(len(kld))]) \
                + self.model_cfg.loss_weights['bin'] * bin_loss \
                + self.model_cfg.loss_weights['adv'] * vae_adv_loss \
                + self.model_cfg.loss_weights['hinge'] * shift_hinge

            disc_real_loss = eval(self.model_cfg.adv_loss)(disc_real, mode='real')
            disc_fake_loss = eval(self.model_cfg.adv_loss)(disc_fake.detach(), mode='fake')
            disc_loss = disc_real_loss + disc_fake_loss

            if mode.__eq__('train'):
                self._backprop(vae_loss, disc_loss)

            iterator.set_description("V: {} | Epoch: {} | {} | Loss: {:.4f}".format(self.exp_cfg.version,
                epochID, mode, vae_loss.item()), refresh=True)

            losses['total_loss'].append(vae_loss.item())
            losses['reconstruction_loss'].append(reconst_loss.item())
            for vae_stage in range(len(kld)):
                losses['KL-divergence-{}'.format(vae_stage)].append(kld[vae_stage].item())
            losses['vae_adv_loss'].append(vae_adv_loss.item())
            losses['disc_loss'].append(disc_loss.item())
            losses['disc_real_loss'].append(disc_real_loss.item())
            losses['disc_fake_loss'].append(disc_fake_loss.item())

            # visualize images from the last batch
            if self.exp_cfg.wandb and i == len(iterator) - 1:
                viz_gt = detach_2_np(batch_data['mask_out'].unsqueeze(1))
                viz_pred = detach_2_np(torch.nn.Softmax(dim=1)(model_out.decoded))
                viz_shift = detach_2_np(model_out.shifted)
                
        losses = self._aggregate_losses(losses)
        self._log_epoch_summary(epochID, mode, losses)
        if self.exp_cfg.wandb:
            wandb_utils.visualize_images(epochID, mode, viz_gt, viz_pred, viz_shift)
        return losses

    def train(self):
        for epochID in range(self.model_cfg.epochs):
            for mode in self.model_cfg.modes:
                losses = self._epoch(mode, epochID)
                if mode == 'val':
                    self.compare_and_save(losses['total_loss'], epochID)


class ShiftGANTrainerBuilder(object):
    """VAEGAN Trainer Builder Class
    """

    def __init__(self):
        """VAEGAN Trainer Builder Class Constructor
        """
        self._instance = None

    def __call__(self, data_cfg, model_cfg, exp_cfg, **_ignored):
        """Callback function
        Args:
            data_cfg (Config): Data Config object
            model_cfg (Config): Model Config object
            exp_cfg (Config): Experiment Config object
        Returns:
            ShiftGANTrainer: Instantiated VAEGAN trainer object
        """
        if not self._instance:
            self._instance = ShiftGANTrainer(data_cfg=data_cfg, model_cfg=model_cfg, exp_cfg=exp_cfg)
        return self._instance
