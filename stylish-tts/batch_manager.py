import math
import gc, json, traceback
import os.path as osp
import torch
from typing import Optional, Callable, Dict, Any, List
from meldataset import (
    FilePathDataset,
    build_dataloader,
    get_frame_count,
    get_padded_time_bin,
)
import utils
from accelerate.accelerator import Accelerator
from text_utils import TextCleaner
from torch.utils.data import DataLoader
import tqdm
import logging
from config_loader import DatasetConfig
from loss_log import LossLog

logger = logging.getLogger(__name__)


class BatchManager:
    def __init__(
        self,
        dataset_config: DatasetConfig,
        log_dir: str,
        probe_batch_max: int = None,
        device: str = "cpu",
        accelerator: Optional["Accelerator"] = None,
        multispeaker: bool = False,
        text_cleaner: TextCleaner = None,
        stage: str = "",
        epoch: int = 1,
        *,
        train,
    ):
        self.train_path: str = dataset_config.train_data
        self.probe_batch_max: int = probe_batch_max
        self.log_dir: str = log_dir
        self.device: str = device
        self.multispeaker: bool = multispeaker
        self.stage: int = stage

        train_list = utils.get_data_path_list(self.train_path)
        if len(train_list) == 0:
            logger.error(f"Could not open train_list {self.train_path}")
            exit()
        self.dataset: FilePathDataset = FilePathDataset(
            train_list,
            dataset_config.wav_path,
            OOD_data=dataset_config.OOD_data,
            min_length=dataset_config.min_length,
            validation=False,
            multispeaker=multispeaker,
            text_cleaner=text_cleaner,
            pitch_path=dataset_config.pitch_path,
        )
        self.time_bins: Dict[int, List[int]] = self.dataset.time_bins()
        self.process_count: int = 1
        if accelerator is not None:
            self.process_count = accelerator.num_processes
            accelerator.even_batches = False
        self.loader: DataLoader = None
        # TODO: Fix this when we untangle the scheduler
        # self.epoch_step_count: int = len(self.loader.batch_sampler)
        self.epoch_step_count: int = 1000
        self.last_oom: int = -1
        self.last_bin: Optional[int] = None
        self.skip_forward: bool = False

    def get_step_count(self) -> int:
        return self.epoch_step_count // self.process_count

    def probe_loop(self, train) -> None:
        if self.process_count > 1:
            exit(
                "--probe_batch must be run with accelerator num_processes set to 1. After running it, distribute the batch_sizes.json files to the log directories and run in DDP"
            )

        train.stage.reset_batch_sizes()
        batch_size = self.probe_batch_max
        time_keys = sorted(list(self.time_bins.keys()))
        max_frame_size = get_frame_count(time_keys[-1])
        iterator = tqdm.tqdm(
            iterable=time_keys,
            desc="Probing",
            total=max_frame_size,
            unit="frames",
            bar_format="{desc} |{bar}| {n_fmt}/{total_fmt}{postfix} ",
            initial=0,
            colour="MAGENTA",
            delay=5,
            leave=False,
        )
        for key in iterator:
            frame_count = get_frame_count(key)
            iterator.update(n=(frame_count - iterator.n))
            last_bin = key
            done = False
            while not done:
                try:
                    if batch_size == 0:
                        train.stage.set_batch_size(key, 0)
                    elif batch_size > 0:
                        iterator.set_postfix({"batch_size": str(batch_size)})
                        # logger.info(
                        #     "Attempting %d/%d @ %d"
                        #     % (frame_count, max_frame_size, batch_size)
                        # )
                        loader = build_dataloader(
                            self.dataset,
                            self.time_bins,
                            num_workers=1,
                            device=self.device,
                            drop_last=True,
                            multispeaker=self.multispeaker,
                            probe_bin=key,
                            probe_batch_size=batch_size,
                            train=train,
                        )
                        loader = train.accelerator.prepare(loader)
                        for _, batch in enumerate(loader):
                            _ = train.stage.train_batch(batch, train)
                            break
                        train.stage.set_batch_size(key, batch_size)
                    done = True
                except Exception as e:
                    if "out of memory" in str(e) or "cufft" in str(e).lower():
                        audio_length = (last_bin * 0.25) + 0.25
                        iterator.clear()
                        train.logger.info(
                            f"TRAIN_BATCH OOM ({last_bin}) @ batch_size {batch_size}: audio_length {audio_length} total audio length {audio_length * batch_size}"
                        )
                        iterator.display()
                        # logger.info("Probe saw OOM -- backing off")
                        import gc

                        train.stage.optimizer.zero_grad()
                        gc.collect()
                        torch.cuda.empty_cache()
                        counting_up = False
                        if batch_size > 0:
                            batch_size -= 1
                    else:
                        iterator.close()
                        logger.error("UNKNOWN EXCEPTION")
                        logger.error("".join(traceback.format_exception(e)))
                        raise e
        train.stage.save_batch_sizes()
        iterator.close()

    def init_epoch(self, train, should_fast_forward=False) -> None:
        self.loader = build_dataloader(
            self.dataset,
            self.time_bins,
            num_workers=32,
            device=self.device,
            drop_last=True,
            multispeaker=self.multispeaker,
            epoch=train.manifest.current_epoch,
            train=train,
        )
        self.loader = train.accelerator.prepare(self.loader)
        if should_fast_forward:
            self.loader = train.accelerator.skip_first_batches(
                self.loader, train.manifest.current_step
            )
        # if not self.batch_dict:
        #     self.probe_loop(train)
        #     self.resume_loader = None
        self.last_oom = -1
        self.last_bin = None
        self.skip_forward = False

        self.epoch_step_count = len(self.loader.batch_sampler)

    def train_iterate(
        self, batch, train, progress_bar=None, debug=False
    ) -> Optional[LossLog]:
        result = None
        max_attempts = 3
        self.last_bin = get_padded_time_bin(batch[0].shape[-1])
        if self.last_bin == self.last_oom and self.skip_forward:
            return result
        elif self.last_bin != self.last_oom:
            self.skip_forward = False
        for attempt in range(1, max_attempts + 1):
            try:
                if debug:
                    batch_size = train.stage.get_batch_size(self.last_bin)
                    audio_length = (self.last_bin * 0.25) + 0.25
                    progress_bar.clear() if progress_bar is not None else None
                    train.logger.info(
                        f"train_batch(i={train.manifest.current_step}, batch={batch_size}, steps={train.manifest.current_total_step}), segment_bin_length={audio_length}, total_audio_in_batch={batch_size * audio_length}"
                    )
                    progress_bar.display() if progress_bar is not None else None
                result = train.stage.train_batch(batch, train)
                break
            except Exception as e:
                batch_size = train.stage.get_batch_size(self.last_bin)
                audio_length = (self.last_bin * 0.25) + 0.25
                if "CUDA out of memory" in str(e) or "cufft" in str(e).lower():
                    progress_bar.clear() if progress_bar is not None else None
                    train.logger.info(
                        f"{attempt * ('*' if attempt < max_attempts else 'X')} "
                        + f"TRAIN_BATCH OOM ({self.last_bin}) @ batch_size {batch_size}: "
                        + f"audio_len {audio_length} total audio leng {audio_length * batch_size} "
                    )
                    progress_bar.display() if progress_bar is not None else None
                    if attempt >= max_attempts:
                        self.skip_forward = True
                    # train.logger.info(e)
                    train.stage.optimizer.zero_grad()
                    if self.last_oom != self.last_bin:
                        self.last_oom = self.last_bin
                        if batch_size > 1:
                            batch_size -= 1
                        train.stage.set_batch_size(self.last_bin, batch_size)
                        train.stage.save_batch_sizes()
                    gc.collect()
                    torch.cuda.empty_cache()
                else:
                    logger.error("".join(traceback.format_exception(e)))
                    raise e
        # train.optimizer.scale(1.0 / math.sqrt(batch[0].shape[0]))
        train.stage.optimizer.scheduler()
        train.stage.optimizer.step_discriminator_schedulers()
        return result
