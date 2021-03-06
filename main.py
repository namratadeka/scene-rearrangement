import os
import wandb
import argparse
from os.path import join, splitext

from trainer import trainer
from utils.logger import set_logger
from config.config import cfg_parser
from utils.utils import seed_everything
from visualization.wandb_utils import init_wandb


if __name__=="__main__":
    seed_everything(seed=0, harsh=False)
    parser = argparse.ArgumentParser(
        description="Scene Rearrangement Training", allow_abbrev=False
    )
    parser.add_argument(
        "-v",
        "--version",
        type=str,
        default="vae-gan-w_3-2-5.yml",
        help="name of the config file to use"
        )
    parser.add_argument(
        "--gpu",
        type=int,
        default=0,
        help="GPU ID"
        )
    parser.add_argument(
        "-w", "--wandb", action="store_true", help="Log to wandb"
    )
    parser.add_argument(
        "--optimization_iterations", type=int, default=1000, help="targetted_optimization"
    )
    (args, unknown_args) = parser.parse_known_args()

    os.environ["CUDA_VISIBLE_DEVICES"] = str(args.gpu)

    cfg = cfg_parser(join("config", args.version))
    cfg["exp_cfg"].version = splitext(args.version)[0]
    cfg["exp_cfg"].run_name = cfg["exp_cfg"].version
    cfg["exp_cfg"].wandb = args.wandb
    cfg["exp_cfg"].optimization_iterations = args.optimization_iterations

    log_file = join(cfg["exp_cfg"].output_location, cfg["exp_cfg"].version + ".log")
    set_logger(log_file)

    if args.wandb:
        init_wandb(cfg.copy())
    
    pipeline = trainer.factory.create(cfg["model_cfg"].trainer_key, **cfg)
    # pipeline.train()

    pipeline.test()