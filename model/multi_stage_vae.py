import torch
from torch import nn
from collections import namedtuple

from model.base import BaseVAE
from model.network import Network


class MultiStageVAE(BaseVAE):
    def __init__(self, model_cfg):
        self.cfg = model_cfg
        self.device = torch.device("cuda" if torch.cuda.is_available() else "cpu")

        super(MultiStageVAE, self).__init__()

    def _build_encoder(self):

        # ----------------------------  METHOD 2 --------------------------------
        # -------------------------------------
        # -----------  Conv encoders  ---------
        # -------------------------------------
        k_size = 3
        stride = 2
        self.encoder0 = nn.ModuleList([Network(self.cfg.network["encoder0"]) for i in range(self.cfg.n_classes)]) # out shape = 16x111x111
        self.encoder1 = nn.ModuleList([])
        self.encoder2 = nn.ModuleList([])
        self.encoder3 = nn.ModuleList([])
        self.encoder4 = nn.ModuleList([])
        for vae_stage in range(self.cfg.n_classes): # a for loop to build the encoders of all our vae stages
            self.encoder1.append(nn.Sequential(nn.Conv2d(32 * (vae_stage+1), 64, k_size, stride),  # out shape = 32x27x27
                                               nn.LeakyReLU(),
                                               nn.MaxPool2d(2, 2)))
            self.encoder2.append(nn.Sequential(nn.Conv2d(64 * (vae_stage+1), 128, k_size, stride),  # out shape = 64x13x13
                                               nn.LeakyReLU()))
            self.encoder3.append(nn.Sequential(nn.Conv2d(128 * (vae_stage+1), 256, k_size, stride), # out shape = 128x6x6 
                                               nn.LeakyReLU()))
            self.encoder4.append(nn.Sequential(nn.Conv2d(256 * (vae_stage+1), 512, k_size, stride), # out shape = 128x2x2
                                               nn.LeakyReLU()))

        # -------------------------------------
        # -----------  the rest  --------------
        # -------------------------------------
        self.encoder_fc = nn.ModuleList([Network(self.cfg.network["encoder_fc"]) for i in range(self.cfg.n_classes)])
        self.fc_mu = nn.ModuleList([Network(self.cfg.network["fc_mu"]) for i in range(self.cfg.n_classes)])
        self.fc_var = nn.ModuleList([Network(self.cfg.network["fc_var"]) for i in range(self.cfg.n_classes)])

    def _build_decoder(self):
        self.decoder_fc = nn.ModuleList([Network(self.cfg.network["decoder_fc"]) for i in range(self.cfg.n_classes)])

        # -------------------------------------
        # -----------  Conv encoders  ---------
        # -------------------------------------
        k_size = 3
        stride = 2

        self.decoder0 = nn.ModuleList([])
        self.decoder1 = nn.ModuleList([])
        self.decoder2 = nn.ModuleList([])
        self.decoder3 = nn.ModuleList([])
        self.decoder4 = nn.ModuleList([])
        for vae_stage in range(self.cfg.n_classes): # a for loop to build the decoders of all our vae stages
            self.decoder0.append(nn.Sequential(nn.ConvTranspose2d(512 * (vae_stage+1), 256, k_size, 3),  # out shape = 64x6x6 
                                               nn.LeakyReLU(),))
            self.decoder1.append(nn.Sequential(nn.ConvTranspose2d(256 * (vae_stage+1), 128, k_size, stride),  # out shape = 32x13x13 
                                               nn.LeakyReLU(),))
            self.decoder2.append(nn.Sequential(nn.ConvTranspose2d(128 * (vae_stage+1), 64, k_size, stride),  # out shape = 16x27x27
                                               nn.LeakyReLU(),
                                               nn.Upsample(scale_factor= 2)))                           # out shape = 16x54x54
            self.decoder3.append(nn.Sequential(nn.ConvTranspose2d(64 * (vae_stage+1), 32, k_size, stride), # out shape = 8x109x109 
                                               nn.LeakyReLU(),))
            self.decoder4.append(nn.Sequential(nn.ConvTranspose2d(32 * (vae_stage+1), 1, k_size, stride),   # out shape = 1x219x219 
                                               nn.Upsample(size=224),))
                                               # nn.Sigmoid()))

    def encode(self, input):
        '''
        Encoding the input (NxCxHxW) to a set of mu and variance lists with length of VAE stages C
        :param input: (Tensor) segmentation mask of NxCxHxW   C = 3 here
        :return: mu_list: (list of Tensors) Mean of the latent Gaussian [Cx B x D]
        :return: logvar_list: (list of Tensor) Standard deviation of the latent Gaussian [C x B x D]
        '''
        result_encoder0 = []
        result_encoder1 = []
        result_encoder2 = []
        result_encoder3 = []
        result_encoder4 = []
        for vae_stage in range(self.cfg.n_classes): # looping to get the outputs of all encoder
            result_encoder0.append(self.encoder0[vae_stage](input[:, vae_stage:vae_stage+1, :, :]))
            result_encoder1.append(self.encoder1[vae_stage](torch.cat(result_encoder0, dim=1)))
            result_encoder2.append(self.encoder2[vae_stage](torch.cat(result_encoder1, dim=1)))
            result_encoder3.append(self.encoder3[vae_stage](torch.cat(result_encoder2, dim=1)))
            result_encoder4.append(self.encoder4[vae_stage](torch.cat(result_encoder3, dim=1)))

        result = [torch.flatten(result_encoder4[vae_stage], start_dim=1) for vae_stage in range(self.cfg.n_classes)]
        result = [self.encoder_fc[vae_stage](result[vae_stage]) for vae_stage in range(self.cfg.n_classes)]

        mu_list = [self.fc_mu[vae_stage](result[vae_stage]) for vae_stage in range(self.cfg.n_classes)]
        log_var_list = [self.fc_var[vae_stage](result[vae_stage])for vae_stage in range(self.cfg.n_classes)]

        return mu_list, log_var_list

    def reparameterize(self, mu, logvar):
        """
        Reparameterization trick to sample from N(mu, var) from N(0,1).
        :param mu: (Tensor) Mean of the latent Gaussian [B x D]
        :param logvar: (Tensor) Standard deviation of the latent Gaussian [B x D]
        :return: (Tensor) [B x D]
        """
        std = torch.exp(0.5 * logvar)
        eps = torch.randn_like(std)
        return eps * std + mu

    def decode(self, z):
        result = [self.decoder_fc[vae_stage](z[vae_stage]) for vae_stage in range(self.cfg.n_classes)]
        # TODO fix this part to be nicer :)
        result = [result[vae_stage].view(-1, 512, 2, 2) for vae_stage in range(self.cfg.n_classes)]

        result_decoder0 = []
        result_decoder1 = []
        result_decoder2 = []
        result_decoder3 = []
        result_decoder4 = []
        for vae_stage in range(self.cfg.n_classes): # looping to get the outputs of all decoder
            result_decoder0.append(self.decoder0[vae_stage](torch.cat(result[:vae_stage+1], dim=1)))
            result_decoder1.append(self.decoder1[vae_stage](torch.cat(result_decoder0, dim=1)))
            result_decoder2.append(self.decoder2[vae_stage](torch.cat(result_decoder1, dim=1)))
            result_decoder3.append(self.decoder3[vae_stage](torch.cat(result_decoder2, dim=1)))
            result_decoder4.append(self.decoder4[vae_stage](torch.cat(result_decoder3, dim=1)))

        result = torch.cat(result_decoder4, dim=1)

        return result

    def forward(self, input):
        mu, log_var = self.encode(input)    # returns a list of mu and log_var for each of the VAE stages
        z = [self.reparameterize(mu[vae_stages], log_var[vae_stages]) for vae_stages in range(self.cfg.n_classes)]

        model_out_tuple = namedtuple(
            "model_out",
            ["decoded", "mu", "log_var"],
        )
        model_out = model_out_tuple(
            self.decode(z), mu, log_var
        )

        return model_out

    def sample(self, num_samples):
        z = [torch.randn(num_samples, self.cfg.latent_dim) for vae_stages in range(self.cfg.n_classes)]
        z = [z[vae_stages].to(self.device) for vae_stages in range(self.cfg.n_classes)]

        samples = self.decode(z)
        return samples


class MultiStageVAEBuilder(object):
    """VAE Model Builder Class
    """

    def __init__(self):
        """VAE Model Builder Class Constructor
        """
        self._instance = None

    def __call__(self, model_cfg, **_ignored):
        """Callback function
        Args:
            model_cfg (ModelConfig): Model Config object
            **_ignored: ignore extra arguments
        Returns:
            VAE: Instantiated VAE network object
        """
        if not self._instance:
            self._instance = MultiStageVAE(model_cfg=model_cfg)
        return self._instance
