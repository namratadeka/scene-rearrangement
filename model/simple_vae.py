import torch
from torch import nn

from model.base import BaseVAE
from model.network import Network


class VAE(BaseVAE):
    def __init__(self, model_cfg):
        self.cfg = model_cfg
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        super(VAE, self).__init__()

    def _build_encoder(self):
        self.encoder = Network(self.cfg.network["encoder"])
        self.encoder_fc = Network(self.cfg.network["encoder_fc"])
        self.fc_mu = Network(self.cfg.network["fc_mu"])
        self.fc_var = Network(self.cfg.network["fc_var"])

    def _build_decoder(self):
        # self.decoder_fc = Network(self.cfg.network["decoder_fc"])
        # self.decoder = Network(self.cfg.network["decoder"])
        pass

    def encode(self, input):
        result = self.encoder(input)
        result = torch.flatten(result, start_dim=1)
        result = self.encoder_fc(result)

        mu = self.fc_mu(result)
        log_var = self.fc_var(result)

        return mu, log_var

    def decode(self, z):
        result = self.decoder_fc(z)
        # result = result.view(-1, )


    def sample(self, num_samples):
        z = torch.randn(num_samples, self.cfg.latent_dim)
        z = z.to(self.device)

        samples = self.decode(z)
        return samples

    def forward(self):
        pass
