# coding: utf-8
import os
import os.path as osp
import time
import random
import numpy as np
import random
import soundfile as sf
import librosa
import gc
import json

import torch
from torch import nn
import torch.nn.functional as F
import torchaudio
import torch.utils.data
import torch.distributed as dist
from huggingface_hub import hf_hub_download

import logging
import utils
from text_utils import TextCleaner

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

import pandas as pd

np.random.seed(1)
random.seed(1)

to_mel = torchaudio.transforms.MelSpectrogram(
    n_mels=80, n_fft=2048, win_length=1200, hop_length=300
)
mean, std = -4, 4


def preprocess(wave):
    # wave_tensor = torch.from_numpy(wave).float()
    wave_tensor = wave
    mel_tensor = to_mel(wave_tensor)
    mel_tensor = (torch.log(1e-5 + mel_tensor.unsqueeze(0)) - mean) / std
    return mel_tensor


class FilePathDataset(torch.utils.data.Dataset):
    def __init__(
        self,
        data_list,
        root_path,
        sr=24000,
        data_augmentation=False,
        validation=False,
        OOD_data="Data/OOD_texts.txt",
        min_length=50,
        multispeaker=False,
        text_cleaner=None,
    ):

        self.cache = {}
        _data_list = [l.strip().split("|") for l in data_list]
        self.data_list = [data if len(data) == 3 else (*data, 0) for data in _data_list]
        self.text_cleaner = text_cleaner
        self.sr = sr

        self.df = pd.DataFrame(self.data_list)

        self.mean, self.std = -4, 4
        self.data_augmentation = data_augmentation and (not validation)
        self.max_mel_length = 192

        self.min_length = min_length
        with open(
            hf_hub_download(
                repo_id="stylish-tts/train-ood-texts",
                repo_type="dataset",
                filename="OOD_texts.txt",
            ),
            "r",
            encoding="utf-8",
        ) as f:
            tl = f.readlines()
        idx = 1 if ".wav" in tl[0].split("|")[0] else 0
        self.ptexts = [t.split("|")[idx] for t in tl]

        self.root_path = root_path
        self.multispeaker = multispeaker

    def time_bins(self):
        print("Calculating sample lengths")
        sample_lengths = []
        for data in self.data_list:
            wave_path = data[0]
            wave, sr = sf.read(osp.join(self.root_path, wave_path))
            wave_len = wave.shape[0]
            if sr != 24000:
                wave_len *= 24000 / sr
            sample_lengths.append(wave_len)
        time_bins = {}
        for i in range(len(sample_lengths)):
            bin_num = get_time_bin(sample_lengths[i])
            if bin_num != -1:
                if bin_num not in time_bins:
                    time_bins[bin_num] = []
                time_bins[bin_num].append(i)
        print("Finished sample lengths")
        return time_bins

    def __len__(self):
        return len(self.data_list)

    def __getitem__(self, idx):
        data = self.data_list[idx]
        path = data[0]
        wave, text_tensor, speaker_id, mel_tensor = self._cache_tensor(data)

        acoustic_feature = mel_tensor.squeeze()
        length_feature = acoustic_feature.size(1)
        acoustic_feature = acoustic_feature[:, : (length_feature - length_feature % 2)]

        # get reference sample
        if self.multispeaker:
            ref_data = (
                (self.df[self.df[2] == str(speaker_id)]).sample(n=1).iloc[0].tolist()
            )
            ref_mel_tensor, ref_label = self._load_data(ref_data[:3])
        else:
            ref_data = []
            ref_mel_tensor, ref_label = None, ""

        # get OOD text

        ps = ""

        while len(ps) < self.min_length:
            rand_idx = np.random.randint(0, len(self.ptexts) - 1)
            ps = self.ptexts[rand_idx]

            text = self.text_cleaner(ps)
            text.insert(0, 0)
            text.append(0)

            ref_text = torch.LongTensor(text)

        return (
            speaker_id,
            acoustic_feature,
            text_tensor,
            ref_text,
            ref_mel_tensor,
            ref_label,
            path,
            wave,
        )

    def _load_tensor(self, data):
        wave_path, text, speaker_id = data
        speaker_id = int(speaker_id)
        wave, sr = sf.read(osp.join(self.root_path, wave_path))
        if wave.shape[-1] == 2:
            wave = wave[:, 0].squeeze()
        if sr != 24000:
            wave = librosa.resample(wave, orig_sr=sr, target_sr=24000)
            print(wave_path, sr)

        pad_start = 5000
        pad_end = 5000
        time_bin = get_time_bin(wave.shape[0])
        if time_bin != -1:
            frame_count = get_frame_count(time_bin)
            pad_start = (frame_count * 300 - wave.shape[0]) // 2
            pad_end = frame_count * 300 - wave.shape[0] - pad_start
        wave = np.concatenate(
            [np.zeros([pad_start]), wave, np.zeros([pad_end])], axis=0
        )
        wave = torch.from_numpy(wave).float()

        text = self.text_cleaner(text)

        text.insert(0, 0)
        text.append(0)

        text = torch.LongTensor(text)

        return wave, text, speaker_id

    def _cache_tensor(self, data):
        path = data[0]
        # if path in self.cache:
        # (wave, text_tensor, speaker_id, mel_tensor) = self.cache[path]
        # else:
        wave, text_tensor, speaker_id = self._load_tensor(data)
        mel_tensor = preprocess(wave).squeeze()
        # self.cache[path] = (wave, text_tensor, speaker_id,
        #                    mel_tensor)
        return (wave, text_tensor, speaker_id, mel_tensor)

    def _load_data(self, data):
        wave, text_tensor, speaker_id, mel_tensor = self._cache_tensor(data)

        mel_length = mel_tensor.size(1)
        if mel_length > self.max_mel_length:
            random_start = np.random.randint(0, mel_length - self.max_mel_length)
            mel_tensor = mel_tensor[
                :, random_start : random_start + self.max_mel_length
            ]

        return mel_tensor, speaker_id


class Collater(object):
    """
    Args:
      adaptive_batch_size (bool): if true, decrease batch size when long data comes.
    """

    def __init__(self, return_wave=False, multispeaker=False):
        self.text_pad_index = 0
        self.min_mel_length = 192
        self.max_mel_length = 192
        self.return_wave = return_wave
        self.multispeaker = multispeaker

    def __call__(self, batch):
        # batch[0] = wave, mel, text, f0, speakerid
        batch_size = len(batch)

        # sort by mel length
        lengths = [b[1].shape[1] for b in batch]
        batch_indexes = np.argsort(lengths)[::-1]
        batch = [batch[bid] for bid in batch_indexes]

        nmels = batch[0][1].size(0)
        max_mel_length = max([b[1].shape[1] for b in batch])
        max_text_length = max([b[2].shape[0] for b in batch])
        max_rtext_length = max([b[3].shape[0] for b in batch])

        labels = torch.zeros((batch_size)).long()
        mels = torch.zeros((batch_size, nmels, max_mel_length)).float()
        texts = torch.zeros((batch_size, max_text_length)).long()
        ref_texts = torch.zeros((batch_size, max_rtext_length)).long()

        input_lengths = torch.zeros(batch_size).long()
        ref_lengths = torch.zeros(batch_size).long()
        output_lengths = torch.zeros(batch_size).long()
        ref_mels = torch.zeros((batch_size, nmels, self.max_mel_length)).float()
        ref_labels = torch.zeros((batch_size)).long()
        paths = ["" for _ in range(batch_size)]
        waves = torch.zeros(
            (batch_size, batch[0][7].shape[-1])
        ).float()  # [None for _ in range(batch_size)]

        for bid, (
            label,
            mel,
            text,
            ref_text,
            ref_mel,
            ref_label,
            path,
            wave,
        ) in enumerate(batch):
            mel_size = mel.size(1)
            text_size = text.size(0)
            rtext_size = ref_text.size(0)
            labels[bid] = label
            mels[bid, :, :mel_size] = mel
            texts[bid, :text_size] = text
            ref_texts[bid, :rtext_size] = ref_text
            input_lengths[bid] = text_size
            ref_lengths[bid] = rtext_size
            output_lengths[bid] = mel_size
            paths[bid] = path
            if self.multispeaker:
                ref_mel_size = ref_mel.size(1)
                ref_mels[bid, :, :ref_mel_size] = ref_mel
                ref_labels[bid] = ref_label
            waves[bid] = wave

        return (
            waves,
            texts,
            input_lengths,
            ref_texts,
            ref_lengths,
            mels,
            output_lengths,
            ref_mels,
        )


def build_dataloader(
    dataset,
    time_bins,
    validation=False,
    batch_size={},
    num_workers=1,
    device="cpu",
    collate_config={},
    probe_bin=None,
    probe_batch_size=None,
    drop_last=True,
    multispeaker=False,
    epoch=1,
):

    collate_config["multispeaker"] = multispeaker
    collate_fn = Collater(**collate_config)
    drop_last = not validation and probe_batch_size is not None
    data_loader = torch.utils.data.DataLoader(
        dataset,
        # batch_size=min(batch_size, len(dataset)),
        # shuffle=(not validation),
        num_workers=num_workers,
        batch_sampler=DynamicBatchSampler(
            time_bins,
            batch_size,
            shuffle=(not validation),
            drop_last=drop_last,
            num_replicas=1,
            rank=0,
            force_bin=probe_bin,
            force_batch_size=probe_batch_size,
            epoch=epoch,
        ),
        # drop_last=(not validation),
        collate_fn=collate_fn,
        pin_memory=(device != "cpu"),
    )

    return data_loader


class DynamicBatchSampler(torch.utils.data.Sampler):
    def __init__(
        self,
        time_bins,
        batch_sizes,
        num_replicas=None,
        rank=None,
        shuffle=True,
        seed=0,
        drop_last=False,
        epoch=1,
        force_bin=None,
        force_batch_size=None,
    ):
        self.time_bins = time_bins
        self.batch_sizes = batch_sizes
        if num_replicas is None:
            self.num_replicas = dist.get_world_size()
        else:
            self.num_replicas = num_replicas
        if rank is None:
            self.rank = dist.get_rank()
        else:
            self.rank = rank
        self.shuffle = shuffle
        self.seed = seed
        self.drop_last = drop_last

        self.epoch = epoch
        self.total_len = 0
        self.last_bin = None

        self.force_bin = force_bin
        self.force_batch_size = force_batch_size
        if force_bin is not None and force_batch_size is not None:
            self.drop_last = False

        total = 0
        for key in self.time_bins.keys():
            total += len(self.time_bins[key])
        for key in self.time_bins.keys():
            val = self.time_bins[key]
            total_batch = self.get_batch_size(key) * num_replicas
            if total_batch > 0:
                self.total_len += len(val) // total_batch
                if not self.drop_last and len(val) % total_batch != 0:
                    self.total_len += 1

    def __iter__(self):
        sampler_order = list(self.time_bins.keys())
        sampler_indices = []
        if self.force_bin is not None:
            sampler_order = [self.force_bin]
            sampler_indices = [0]
        elif self.shuffle:
            g = torch.Generator()
            g.manual_seed(self.seed + self.epoch)
            sampler_indices = torch.randperm(len(sampler_order), generator=g).tolist()
        else:
            sampler_indices = list(range(len(sampler_order)))

        for index in sampler_indices:
            key = sampler_order[index]
            if self.get_batch_size(key) <= 0:
                continue
            current_bin = self.time_bins[key]
            dist = torch.utils.data.distributed.DistributedSampler(
                current_bin,
                num_replicas=self.num_replicas,
                rank=self.rank,
                shuffle=self.shuffle,
                seed=self.seed,
                drop_last=self.drop_last,
            )
            dist.set_epoch(self.epoch)
            sampler = torch.utils.data.sampler.BatchSampler(
                dist, self.get_batch_size(key), self.drop_last
            )
            # print(key, self.get_batch_size(key))
            for item_list in sampler:
                self.last_bin = key
                yield [current_bin[i] for i in item_list]

    def __len__(self):
        return self.total_len

    def set_epoch(self, epoch):
        self.epoch = epoch

    def probe_batch(self, new_bin, batch_size):
        self.force_bin = new_bin
        if len(self.time_bins[new_bin]) < batch_size:
            batch_size = len(self.time_bins[new_bin])
        self.force_batch_size = batch_size
        return batch_size

    def get_batch_size(self, key):
        result = 1
        if self.force_batch_size is not None:
            result = self.force_batch_size
        elif str(key) in self.batch_sizes:
            result = self.batch_sizes[str(key)]
        return result


class BatchManager:
    def __init__(
        self,
        train_path,
        log_dir,
        probe_batch=None,
        root_path="",
        OOD_data=[],
        min_length=50,
        device="cpu",
        accelerator=None,
        log_print=None,
        multispeaker=False,
        text_cleaner=None,
    ):
        self.train_path = train_path
        self.probe_batch = probe_batch
        self.log_dir = log_dir
        self.log_print = log_print
        self.device = device
        self.multispeaker = multispeaker

        self.batch_dict = {}
        if self.probe_batch is None:
            batch_file = osp.join(self.log_dir, "batch_sizes.json")
            if osp.isfile(batch_file):
                with open(batch_file, "r") as batch_input:
                    self.batch_dict = json.load(batch_input)
        train_list = utils.get_data_path_list(self.train_path)
        if len(train_list) == 0:
            print("Could not open train_list", self.train_path)
            exit()
        self.dataset = FilePathDataset(
            train_list,
            root_path,
            OOD_data=OOD_data,
            min_length=min_length,
            validation=False,
            multispeaker=multispeaker,
            text_cleaner=text_cleaner,
        )
        self.time_bins = self.dataset.time_bins()
        self.process_count = 1
        if accelerator is not None:
            self.process_count = accelerator.num_processes
            accelerator.even_batches = False
        loader = build_dataloader(
            self.dataset,
            self.time_bins,
            batch_size=self.batch_dict,
            num_workers=32,
            device=device,
            drop_last=True,
            multispeaker=multispeaker,
        )
        self.epoch_step_count = len(loader.batch_sampler)

    def get_step_count(self):
        return self.epoch_step_count // self.process_count

    def get_batch_size(self, i):
        batch_size = 1
        if str(i) in self.batch_dict:
            batch_size = self.batch_dict[str(i)]
        return batch_size

    def set_batch_size(self, i, batch_size):
        self.batch_dict[str(i)] = batch_size

    def save_batch_dict(self):
        batch_file = osp.join(self.log_dir, "batch_sizes.json")
        with open(batch_file, "w") as o:
            json.dump(self.batch_dict, o)

    def epoch_loop(self, train, debug=False):
        if self.probe_batch is not None:
            self.probe_loop(train)
        else:
            self.train_loop(train=train, debug=debug)

    def probe_loop(self, train):
        if self.process_count > 1:
            exit(
                "--probe_batch must be run with accelerator num_processes set to 1. After running it, distribute the batch_sizes.json files to the log directories and run in DDP"
            )

        self.batch_dict = {}
        batch_size = self.probe_batch
        time_keys = sorted(list(self.time_bins.keys()))
        max_frame_size = get_frame_count(time_keys[-1])
        for key in time_keys:
            frame_count = get_frame_count(key)
            last_bin = key
            done = False
            while not done:
                try:
                    if batch_size == 1:
                        self.set_batch_size(key, 1)
                        done = True
                    elif batch_size > 0:
                        print(
                            "Attempting %d/%d @ %d"
                            % (frame_count, max_frame_size, batch_size)
                        )
                        # TODO: this is the epochs itteration not total itterations maybe we should change the name
                        train.manifest.iters = 0
                        loader = build_dataloader(
                            self.dataset,
                            self.time_bins,
                            batch_size=self.batch_dict,
                            num_workers=1,
                            device=self.device,
                            drop_last=True,
                            multispeaker=self.multispeaker,
                            probe_bin=key,
                            probe_batch_size=batch_size,
                        )

                        loader = train.accelerator.prepare(loader)
                        for _, batch in enumerate(loader):
                            _, _ = train.train_batch(
                                i=0, batch=batch, running_loss=0, iters=0, train=train
                            )
                            break
                        self.set_batch_size(key, batch_size)
                    done = True
                except Exception as e:
                    if "out of memory" in str(e):
                        audio_length = (last_bin * 0.25) + 0.25
                        self.log_print(
                            f"TRAIN_BATCH OOM ({last_bin}) @ batch_size {batch_size}: audio_length {audio_length} total audio length {audio_length * batch_size}"
                        )
                        print("Probe saw OOM -- backing off")
                        import gc

                        train.optimizer.zero_grad()
                        gc.collect()
                        torch.cuda.empty_cache()
                        counting_up = False
                        if batch_size > 1:
                            batch_size -= 1
                    else:
                        print("UNKNOWN EXCEPTION")
                        raise e
        self.save_batch_dict()

    def train_loop(self, train, debug=False):
        running_loss = 0
        iters = 0
        last_oom = -1
        max_attempts = 3
        loader = build_dataloader(
            self.dataset,
            self.time_bins,
            batch_size=self.batch_dict,
            num_workers=32,
            device=self.device,
            drop_last=True,
            multispeaker=self.multispeaker,
            epoch=train.manifest.current_epoch,
        )
        self.epoch_step_count = len(loader.batch_sampler)
        loader = train.accelerator.prepare(loader)
        for i, batch in enumerate(loader):
            last_bin = get_time_bin(batch[0].shape[-1])
            for attempt in range(1, max_attempts + 1):
                try:
                    if debug:
                        batch_size = self.get_batch_size(last_bin)
                        audio_length = (last_bin * 0.25) + 0.25
                        self.log_print(
                            f"train_batch(i={i}, batch={batch_size}, running_loss={running_loss}, iters={iters}), segment_bin_length={audio_length}, total_audio_in_batch={batch_size * audio_length}"
                        )
                    running_loss, iters = train.train_batch(
                        i, batch, running_loss, iters, train
                    )
                    break
                except Exception as e:
                    batch_size = self.get_batch_size(last_bin)
                    audio_length = (last_bin * 0.25) + 0.25
                    if "CUDA out of memory" in str(e):
                        self.log_print(
                            f"{attempt * ('⚠️' if attempt < max_attempts else '❌')}\n"
                            f"TRAIN_BATCH OOM ({last_bin}) @ batch_size {batch_size}: audio_length {audio_length} total audio length {audio_length * batch_size}"
                        )
                        self.log_print(e)
                        train.optimizer.zero_grad()
                        if last_oom != last_bin:
                            last_oom = last_bin
                            if batch_size > 1:
                                batch_size -= 1
                            self.set_batch_size(last_bin, batch_size)
                            self.save_batch_dict()
                        gc.collect()
                        torch.cuda.empty_cache()
                    else:
                        raise e


def get_frame_count(i):
    return i * 20 + 20 + 40


def get_time_bin(sample_count):
    result = -1
    frames = sample_count // 300
    if frames >= 20:
        result = (frames - 20) // 20
    return result
