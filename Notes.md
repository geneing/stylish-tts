
## Commands

uv run python stylish-dataset/all-pitch.py --wavdir ../../Data/ --trainpath ../train_list_LibriTTSR_espeak.txt --valpath ../val_list_LibriTTSR_espeak.txt --split 96 --outpath pitch.safetensors

uv run python stylish-dataset/all-pitch.py --wavdir ../../Data/ --trainpath ../train_list_LibriTTSR_espeak.short.txt --valpath ../val_list_LibriTTSR_espeak.short.txt --split 2 --outpath pitch.safetensors


uv run python stylish-tts/train.py \
    --model_config_path config/model.vocos.yml \
    --config_path config/config_librittsr.yml \
    --stage alignment \
    --out_dir output

## Vocoders
- [FreeV](https://github.com/BakerBunker/FreeV)
- 

## Parts of the pipeline:
- [UTMOS](https://github.com/gemelo-ai/vocos/blob/main/metrics/UTMOS.py)
- [Periodicty metric](https://github.com/gemelo-ai/vocos/blob/main/metrics/periodicity.py)


## Ops:

- [Spectral Ops](https://github.com/gemelo-ai/vocos/blob/main/vocos/spectral_ops.py)
- [Feature Loss, Generator/Discriminator Loss](https://github.com/gemelo-ai/vocos/blob/main/vocos/loss.py)
- [MultiPeriodDiscriminator](https://github.com/gemelo-ai/vocos/blob/main/vocos/discriminators.py)
- [Multiresolution Spectral Discriminator](https://github.com/descriptinc/descript-audio-codec/blob/main/dac/model/discriminator.py)
- [Snake Activation](https://github.com/hubertsiuzdak/snac/blob/main/snac/layers.py)
- [VQ](https://github.com/facebookresearch/encodec/tree/main/encodec/quantization)



## Other libraries
- [Echogarden alignment/library](https://github.com/echogarden-project/echogarden)
- [goruut](https://github.com/neurlang/goruut)
- [misaki](https://github.com/hexgrad/misaki)


