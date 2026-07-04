Tested on Ubuntu and Python 3


A simple script to download all your favorite Arknights OSTs from monster-siren.hypergryph.com

Download all songs, albums and fill out metadata, album, cover art, artists and even lyrics

### Requirements:

Python

ffmpeg

```pip3 install -r requirements.txt``` or ```pip install -r requirements.txt```

### Usage

Make sure you have installed **ffmpeg** and can run `ffmpeg` in your terminal. If you want to use the `--rsgain` option, also install **rsgain** and put it in your PATH.

#### Default run (FLAC)

```python3 main.py``` or ```python main.py```

Downloads every album into `./MonsterSiren/`, embeds cover art, lyrics and metadata, and converts WAV sources to FLAC.

#### Choosing output format

The API offers `.mp3` and `.wav`. WAV sources are converted to the format you choose, while MP3-sourced songs are always saved as `.mp3`:

```python3 main.py flac   # default — convert WAV sources to FLAC
python3 main.py mp3    # convert WAV sources to 320 kbps MP3
python3 main.py wav    # keep WAV sources as-is
```

> Note: `.wav` cannot store metadata, so choosing `wav` means no tags, cover art or lyrics will be embedded for WAV-sourced songs.

#### Download a specific album or song

By default the script downloads every album. To grab just one album or one song, pass its CID (the identifier used in the Monster Siren site URLs/API). The two options are mutually exclusive:

```
python3 main.py --album <CID>   # download one full album
python3 main.py --song <CID>    # download one single song
```

They combine with the format and replaygain options:

```
python3 main.py mp3 --album <CID>
python3 main.py flac --song <CID> --rsgain
```

> A single song is saved under its parent album folder (`./MonsterSiren/<album>/`) with the album's cover art and metadata, but the album is **not** marked as completed — so a later full run will still download the rest of it. An explicit `--album` request re-downloads even if that album is already in `completed_albums.json`.

#### ReplayGain tagging

Apply ReplayGain tags with [rsgain](https://github.com/complexlogic/rsgain) after downloading:

```python3 main.py flac --rsgain
```

This runs `rsgain custom -a -si -S` over all downloaded audio files in batches. Leave out the flag if rsgain is not installed.

#### Behavior

- Albums are saved under `./MonsterSiren/<album>/`.
- Completed albums are tracked in `./MonsterSiren/completed_albums.json`, so re-running the script automatically **skips** albums already downloaded.
- Albums fetched in the latest run are listed in `./MonsterSiren/new_albums.json`.


![image](https://user-images.githubusercontent.com/80285371/207703442-a96488bc-5642-4d7b-92da-f0ac976e944b.png)
![image](https://user-images.githubusercontent.com/80285371/207703484-2271b5a1-7928-401d-9bed-a5e4feeec4d0.png)
