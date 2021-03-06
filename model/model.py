from model.simple_vae import VAEBuilder
from model.multi_stage_vae import MultiStageVAEBuilder
from model.markov_vae import MarkovVAEBuilder
from model.vae_gan import VAEGANBuilder
from model.shift_gan import ShiftGANBuilder


class ModelFactory(object):
    """Factory class to build new model objects
    """

    def __init__(self):
        self._builders = dict()

    def register_builder(self, key, builder):
        """Registers a new model builder into the factory
        Args:
            key (str): string key of the model builder
            builder (any): Builder object
        """
        self._builders[key] = builder

    def create(self, key, **kwargs):
        """Instantiates a new builder object, once it's registered
        Args:
            key (str): string key of the model builder
            **kwargs: keyword arguments
        Returns:
            any: Returns an instance of a model object correspponding to the model builder
        Raises:
            ValueError: If model builder is not registered, raises an exception
        """
        builder = self._builders.get(key)
        if not builder:
            raise ValueError(key)
        return builder(**kwargs)


factory = ModelFactory()
factory.register_builder("simple_vae", VAEBuilder())
factory.register_builder("multi_stage_vae", MultiStageVAEBuilder())
factory.register_builder("markov_vae", MarkovVAEBuilder())
factory.register_builder("vae_gan", VAEGANBuilder())
factory.register_builder("shift_gan", ShiftGANBuilder())
