import os
import requests
from tqdm import tqdm
import pylrc
import json
import argparse
import subprocess
import glob
import shutil
import sys

from PIL import Image
from multiprocessing import Pool, Manager
from mutagen.easyid3 import EasyID3
from mutagen.id3 import APIC, SYLT, Encoding, ID3
from mutagen.flac import Picture, FLAC
from pydub import AudioSegment

def parse_arguments():
    parser = argparse.ArgumentParser(description='Downloads music directly from the Monster Siren Records website.')
    parser.add_argument('format', nargs='?', default='flac', choices=['flac', 'mp3', 'wav'], help='target format for the songs the api provides as .wav (default: flac)')
    parser.add_argument('--rsgain', action='store_true', help='run rsgain on downloaded files after completion')
    group = parser.add_mutually_exclusive_group()
    group.add_argument('--song', metavar='CID', help='download a single song by its CID')
    group.add_argument('--album', metavar='CID', help='download a single album by its CID')
    args = parser.parse_args()
    return args.format, args.rsgain, args.song, args.album

def make_valid(filename):
    # Make a filename valid in different OSs
    f = filename.replace(':', '_')
    f = f.replace('/', '_')
    f = f.replace('<', '_')
    f = f.replace('>', '_')
    f = f.replace('\'', '_')
    f = f.replace('\\', '_')
    f = f.replace('|', '_')
    f = f.replace('?', '_')
    f = f.replace('*', '_')
    f = f.replace(' ', '_')
    return f


def load_completed_albums(path, mutex):
    """Load and validate completed_albums.json.

    Returns the dict on success. Exits with a remediation message on any
    format mismatch — callers can assume the return is a dict of
    {album_name: [cid, ...]}.
    """
    try:
        with mutex:
            with open(path, 'r', encoding='utf8') as f:
                data = json.load(f)
    except FileNotFoundError:
        return {}
    except json.JSONDecodeError as e:
        _abort_bad_state(path, f"corrupt JSON: {e}")

    if not isinstance(data, dict):
        _abort_bad_state(path, f"expected dict, got {type(data).__name__}")
    for name, cids in data.items():
        if not isinstance(cids, list):
            _abort_bad_state(
                path,
                f"album {name!r} has non-list value {type(cids).__name__}"
            )
    return data


def _abort_bad_state(path, reason):
    sys.exit(
        f"Error: {path} is incompatible ({reason}).\n\n"
        "The downloader refuses to proceed automatically to avoid an "
        "accidental full library re-download. Either:\n\n"
        "  1. Migrate (preserves existing downloads):\n"
        "       python /scripts/audit-library.py --apply\n\n"
        "  2. Discard state and re-download everything:\n"
        f"       rm {path} && python main.py mp3 --rsgain\n"
    )


def lyric_file_to_text(filename):
    lrc_file = open(filename, 'r', encoding='utf-8')
    lrc_string = ''.join(lrc_file.readlines())
    lrc_file.close()
    subs = pylrc.parse(lrc_string)
    ret = []
    for sub in subs:
        time = int(sub.time * 1000)
        text = sub.text
        ret.append((text, time))
    return ret

def update_downloaded_albums(queue, directory, mutex):
    """Collects albums downloaded in current session and writes to both
    completed_albums.json and new_albums.json.

    Queue items are (album_name, [song_cids]) tuples. The CID list is the
    authoritative set of songs the album currently contains per the API —
    it replaces any previously stored list for that album, so tracks added
    after first download are correctly tracked.
    """
    updated = []  # list of (name, [cids])
    while True:
        item = queue.get()
        # Final queue element, guaranteed to happen after all maps completed
        if item is None:
            break
        updated.append(item)

    state_path = directory + 'completed_albums.json'
    completed_albums = load_completed_albums(state_path, mutex)

    # Append new albums to completed_albums.json
    for album_name, cids in updated:
        completed_albums[album_name] = cids

    with mutex:
        with open(state_path, 'w+', encoding='utf8') as f:
            json.dump(completed_albums, f, ensure_ascii=False, indent=2)

    # Write new albums from this session to new_albums.json (overwrite)
    new_album_names = [name for name, _ in updated]
    with mutex:
        with open(directory + 'new_albums.json', 'w', encoding='utf8') as f:
            json.dump(new_album_names, f, ensure_ascii=False, indent=2)


def run_rsgain(directory):
    """Execute rsgain custom -a -si -S recursively on audio files in the download directory"""
    # Find rsgain executable
    rsgain_cmd = shutil.which('rsgain')
    if rsgain_cmd is None:
        print("Warning: rsgain not found in PATH. Please install rsgain to use this feature.")
        return

    try:
        print("\nRunning rsgain on downloaded files...")
        # Find all audio files recursively
        audio_files = []
        for ext in ['*.flac', '*.mp3', '*.wav', '*.m4a', '*.aac', '*.ogg', '*.opus', '*.wma']:
            audio_files.extend(glob.glob(os.path.join(directory, '**', ext), recursive=True))

        if not audio_files:
            print("No audio files found for rsgain processing")
            return

        print(f"Found {len(audio_files)} audio files to process...")

        # Process in batches
        # Use a conservative batch size of 100 files per batch
        batch_size = 100
        total_batches = (len(audio_files) + batch_size - 1) // batch_size
        for i in range(0, len(audio_files), batch_size):
            batch = audio_files[i:i + batch_size]
            print(f"Processing batch {i // batch_size + 1}/{total_batches}...")
            subprocess.run(
                [rsgain_cmd, 'custom', '-a', '-si', '-S'] + batch,
                check=True
            )

        print("rsgain completed successfully")
    except subprocess.CalledProcessError as e:
        print(f"Error running rsgain")


def fill_metadata(filename, filetype, album, title, albumartist, artist, tracknumber, albumcover, songlyricpath):
    if filetype == '.mp3':
        file =  EasyID3(filename)
    else:
        file = FLAC(filename)

    file['album'] = album
    file['title'] = title
    file['albumartist'] = ''.join(albumartist)
    file['artist'] = ''.join(artist)
    file['tracknumber'] = str(tracknumber + 1)
    file.save()

    if filetype == '.mp3':
        file = ID3(filename)
        file.add(APIC(mime='image/png',type=3,desc='Cover',data=open(albumcover,'rb').read()))
        # Read and add lyrics
        if (songlyricpath != None):
            sylt = lyric_file_to_text(songlyricpath)
            file.setall('SYLT', [SYLT(encoding=Encoding.UTF8, lang='eng', format=2, type=1, text=sylt)])
        file.save()
    else:
        image = Picture()
        image.type = 3
        image.desc = 'Cover'
        image.mime = 'image/png'
        with open(albumcover,'rb') as f:
            image.data = f.read()
        with Image.open(albumcover) as imagePil:
            image.width, image.height = imagePil.size
            image.depth = 24
        file.add_picture(image)
        # Read and add lyrics
        if (songlyricpath != None):
            musiclrc = open(songlyricpath, 'r', encoding='utf-8').read()
            file['lyrics'] = musiclrc
        file.save()

    return 


def download_song(session, directory, name, url, target_format):
    source = session.get(url, stream=True)
    filename = directory + '/' + make_valid(name)
    filetype = ''

    if source.headers['content-type'] == 'audio/mpeg':
        filename += '.mp3'
        filetype = '.mp3'
    else:
        filename += '.wav'

    # Download song
    total = int(source.headers.get('content-length', 0))
    with open(filename, 'w+b') as f, tqdm(
        desc=name,
        total=total,
        unit='iB',
        unit_scale=True,
        unit_divisor=1024,
    ) as bar:
        for data in source.iter_content(chunk_size = 1024):
            size = f.write(data)
            bar.update(size)

    # If file is .wav then export to desired format
    if source.headers['content-type'] != 'audio/mpeg':
        if target_format == 'flac':
            AudioSegment.from_wav(filename).export(
                directory + '/' + make_valid(name) + '.flac', 
                format='flac'
            )
        if target_format == 'mp3':
            AudioSegment.from_wav(filename).export(
                directory + '/' + make_valid(name) + '.mp3', 
                format='mp3',
                bitrate='320k'
            )
        os.remove(filename)
        filename = directory + '/' + make_valid(name) + '.' + target_format
        filetype = '.' + target_format
        
    return filename, filetype


def download_album_cover(session, album_dir, cover_url):
    """Download album art into album_dir as cover.png, converting from jpg. Returns the cover path."""
    with open(album_dir + '/cover.jpg', 'w+b') as f:
        f.write(session.get(cover_url).content)
    cover = Image.open(album_dir + '/cover.jpg')
    cover.save(album_dir + '/cover.png')
    os.remove(album_dir + '/cover.jpg')
    return album_dir + '/cover.png'


def download_album(args):
    directory = args['directory']
    session = args['session']
    queue = args['queue']
    mutex = args['mutex']
    target_format = args['target_format']
    force = args.get('force', False)

    album_cid = args['cid']
    album_name = args['name']
    album_coverUrl = args['coverUrl']
    album_artistes = args['artistes']
    album_url = 'https://monster-siren.hypergryph.com/api/album/' + album_cid + '/detail'

    # Fetch the authoritative song list first.
    # Skip if there are no new tracks in an album.
    songs = session.get(album_url, headers={'Accept': 'application/json'}).json()['data']['songs']
    all_cids = [s['cid'] for s in songs]

    completed_albums = load_completed_albums(
        directory + 'completed_albums.json', mutex
    )
    stored_cids = completed_albums.get(album_name, [])

    if force:
        pending = songs
    elif not stored_cids:
        pending = songs
    else:
        pending = [s for s in songs if s['cid'] not in stored_cids]

    if not pending:
        print(f'Skipping downloaded album {album_name}')
        queue.put((album_name, all_cids))
        return

    try:
        os.mkdir(directory + make_valid(album_name))
    except FileExistsError:
        pass

    # Download album art
    download_album_cover(session, directory + make_valid(album_name), album_coverUrl)

    pending_cid_set = {s['cid'] for s in pending}
    for song_track_number, song in enumerate(songs):
        if song['cid'] not in pending_cid_set:
            continue

        # Get song details
        song_cid = song['cid']
        song_name = song['name']
        song_artists = song['artistes']
        song_url = 'https://monster-siren.hypergryph.com/api/song/' + song_cid
        song_detail = session.get(song_url, headers={'Accept': 'application/json'}).json()['data']
        song_lyricUrl = song_detail['lyricUrl']
        song_sourceUrl = song_detail['sourceUrl']

        # Download lyric
        if (song_lyricUrl != None):
            songlyricpath = directory + make_valid(album_name) + '/' + make_valid(song_name) + '.lrc'
            with open(songlyricpath, 'w+b') as f:
                f.write(session.get(song_lyricUrl).content)
        else:
            songlyricpath = None

        # Download song and fill out metadata
        filename, filetype = download_song(session=session, directory=directory + make_valid(album_name), name=song_name, url=song_sourceUrl, target_format=target_format)
        fill_metadata(filename=filename,
                        filetype=filetype,
                        album=album_name,
                        title=song_name,
                        albumartist=album_artistes,
                        artist=song_artists,
                        tracknumber=song_track_number,
                        albumcover=directory + make_valid(album_name) + '/cover.png',
                        songlyricpath=songlyricpath)
    
    # Mark album as finished
    queue.put((album_name, all_cids))

    return


def download_single_song(session, directory, target_format, song_cid, albums):
    """Download a single song by CID, locating its parent album for metadata and cover art."""
    print(f'Searching for song {song_cid}...')
    found_album = None
    song = None
    song_track_number = None

    for album in albums:
        album_url = 'https://monster-siren.hypergryph.com/api/album/' + album['cid'] + '/detail'
        songs = session.get(album_url, headers={'Accept': 'application/json'}).json()['data']['songs']
        for idx, s in enumerate(songs):
            if s['cid'] == song_cid:
                found_album = album
                song = s
                song_track_number = idx
                break
        if found_album is not None:
            break

    if found_album is None:
        print(f'Error: song CID {song_cid} was not found in any album')
        sys.exit(1)

    album_name = found_album['name']
    album_artistes = found_album['artistes']
    album_coverUrl = found_album['coverUrl']
    song_name = song['name']
    song_artists = song['artistes']
    album_dir = directory + make_valid(album_name)

    try:
        os.mkdir(album_dir)
    except:
        pass

    download_album_cover(session, album_dir, album_coverUrl)

    # Get song details (lyric + source)
    song_url = 'https://monster-siren.hypergryph.com/api/song/' + song_cid
    song_detail = session.get(song_url, headers={'Accept': 'application/json'}).json()['data']
    song_lyricUrl = song_detail['lyricUrl']
    song_sourceUrl = song_detail['sourceUrl']

    # Download lyric
    if song_lyricUrl is not None:
        songlyricpath = album_dir + '/' + make_valid(song_name) + '.lrc'
        with open(songlyricpath, 'w+b') as f:
            f.write(session.get(song_lyricUrl).content)
    else:
        songlyricpath = None

    # Download song and fill out metadata
    filename, filetype = download_song(session=session, directory=album_dir, name=song_name, url=song_sourceUrl, target_format=target_format)
    fill_metadata(filename=filename,
                    filetype=filetype,
                    album=album_name,
                    title=song_name,
                    albumartist=album_artistes,
                    artist=song_artists,
                    tracknumber=song_track_number,
                    albumcover=album_dir + '/cover.png',
                    songlyricpath=songlyricpath)

    print(f'Downloaded song {song_name}')
    return


def main():
    directory = './MonsterSiren/'
    session = requests.Session()
    manager = Manager()
    queue = manager.Queue()
    mutex = manager.Lock()
    target_format, use_rsgain, song_cid, album_cid = parse_arguments()

    try:
        os.mkdir(directory)
    except:
        pass

    
    # Get all albums
    albums = session.get('https://monster-siren.hypergryph.com/api/albums', headers={'Accept': 'application/json'}).json()['data']

    if song_cid:
        # Single song: locate parent album and download just that song
        download_single_song(session, directory, target_format, song_cid, albums)
    else:
        # Album-level download (either a single requested album, or all albums)
        force = False
        if album_cid:
            matching = [a for a in albums if a['cid'] == album_cid]
            if not matching:
                print(f'Error: album CID {album_cid} was not found')
                sys.exit(1)
            albums = matching
            force = True

        for album in albums:
            album['directory'] = directory
            album['session'] = session
            album['queue'] = queue
            album['mutex'] = mutex
            album['target_format'] = target_format
            album['force'] = force


        with Pool(maxtasksperchild=1) as pool:
            pool.apply_async(update_downloaded_albums, (queue, directory, mutex))
            pool.map(download_album, albums)
            queue.put(None)
            pool.close()
            pool.join()

    # Run rsgain if requested
    if use_rsgain:
        run_rsgain(directory)

    return



if __name__ == '__main__':
    main()