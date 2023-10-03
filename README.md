# StyleTTS 2: Towards Human-Level Text-to-Speech through Style Diffusion and Adversarial Training with Large Speech Language Models

### Yinghao Aaron Li, Cong Han, Vinay S. Raghavan, Gavin Mischler, Nima Mesgarani

> In this paper, we present StyleTTS 2, a text-to-speech (TTS) model that leverages style diffusion and adversarial training with large speech language models (SLMs) to achieve human-level TTS synthesis. StyleTTS 2 differs from its predecessor by modeling styles as a latent random variable through diffusion models to generate the most suitable style for the text without requiring reference speech, achieving efficient latent diffusion while benefiting from the diverse speech synthesis offered by diffusion models. Furthermore, we employ large pre-trained SLMs, such as WavLM, as discriminators with our novel differentiable duration modeling for end-to-end training, resulting in improved speech naturalness. StyleTTS 2 surpasses human recordings on the single-speaker LJSpeech dataset and matches it on the multispeaker VCTK dataset as judged by native English speakers. Moreover, when trained on the LibriTTS dataset, our model outperforms previous publicly available models for zero-shot speaker adaptation. This work achieves the first human-level TTS synthesis on both single and multispeaker datasets, showcasing the potential of style diffusion and adversarial training with large SLMs.

Paper: [https://arxiv.org/abs/2306.07691](https://arxiv.org/abs/2306.07691)

Audio samples: [https://styletts2.github.io/](https://styletts2.github.io/)

## TODO
- [x] Training and inference demo code for single-speaker models (LJSpeech)
- [ ] Test training code for multi-speaker models (VCTK and LibriTTS)
- [ ] Finish demo code for multispeaker model and uploade pre-trained models
- [ ] Fix DDP (accelerator) for train_second.py

## Pre-requisites
1. Python >= 3.7
2. Clone this repository:
```bash
git clone https://github.com/yl4579/StyleTTS2.git
cd StyleTTS2
```
3. Install python requirements: 
```bash
pip install SoundFile torchaudio munch torch pydub pyyaml librosa nltk matplotlib accelerate transformers phonemizer einops einops-exts tqdm typing typing-extensions git+https://github.com/resemble-ai/monotonic_align.git
```
4. Download and extract the [LJSpeech dataset](https://keithito.com/LJ-Speech-Dataset/), unzip to the data folder and upsample the data to 24 kHz. The text aligner and pitch extractor are pre-trained on 24 kHz data, but you can easily change the preprocessing and re-train them using your own preprocessing. 
For LibriTTS, you will need to combine train-clean-360 with train-clean-100 and rename the folder train-clean-460 (see [val_list_libritts.txt](https://github.com/yl4579/StyleTTS/blob/main/Data/val_list_libritts.txt) as an example).

## Training
First stage training:
```bash
accelerate launch train_first.py --config_path ./Configs/config.yml
```
Second stage training **(DDP version not working, so the current version uses DP)**:
```bash
python train_second.py --config_path ./Configs/config.yml
```
You can run both consecutively and it will train both the first and second stages. The model will be saved in the format "epoch_1st_%05d.pth" and "epoch_2nd_%05d.pth". Checkpoints and Tensorboard logs will be saved at `log_dir`. 

The data list format needs to be `filename.wav|transcription|speaker`, see [val_list.txt](https://github.com/yl4579/StyleTTS2/blob/main/Data/val_list.txt) as an example. The speaker labels are needed for multi-speaker models because we need to sample reference audio for style diffusion model training. 

### Important Configurations
In [config.yml](https://github.com/yl4579/StyleTTS2/blob/main/Configs/config.yml), there are a few important configurations to take care of:
- `OOD_data`: The path for out-of-distribution texts for SLM adversarial training. The format should be `text|anything`.
- `min_length`: Minimum length of OOD texts for training. This is to make sure the synthesized speech has a minimum length.
- `multispeaker`: Set to true if you want to train a multispeaker model. This is needed because the architecture of the denoiser is different for single and multispeaker models.
- `batch_percentage`: This is to make sure during SLM adversarial training there are no out-of-memory (OOM) issues. If you encounter OOM problem, please set a lower number for this. 
- 
## Inference
Please refer to [inference.ipynb](https://github.com/yl4579/StyleTTS2/blob/main/Demo/Inference_LJSpeech.ipynb) for details. 

The pretrained StyleTTS 2 on LJSpeech corpus in 24 kHz can be downloaded at [StyleTTS 2 Link](https://drive.google.com/file/d/1K3jt1JEbtohBLUA0X75KLw36TW7U1yxq/view?usp=sharing).

**The pretrained model on LibriTTS is currently WIP.**

## References
- [archinetai/audio-diffusion-pytorch](https://github.com/archinetai/audio-diffusion-pytorch)
- [jik876/hifi-gan](https://github.com/jik876/hifi-gan)
- [rishikksh20/iSTFTNet-pytorch](https://github.com/rishikksh20/iSTFTNet-pytorch)
- [nii-yamagishilab/project-NN-Pytorch-scripts/project/01-nsf](https://github.com/nii-yamagishilab/project-NN-Pytorch-scripts/tree/master/project/01-nsf)
