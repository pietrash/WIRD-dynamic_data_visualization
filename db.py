import asyncio

import duckdb
import pandas as pd
import requests
import threading

MAX_SONGS = 100


class Database:
    def __init__(self):
        self.lock = threading.Lock()
        self.con = duckdb.connect('duck.db')
        self.con.execute('CREATE SCHEMA IF NOT EXISTS cache')
        self.con.execute('SET search_path TO cache')

        self.con.execute('''
            CREATE TABLE IF NOT EXISTS cache.song (
                artist_title TEXT PRIMARY KEY,
                artist TEXT,
                title TEXT,
                cover_url TEXT,
                genres TEXT
            );

            CREATE TABLE IF NOT EXISTS cache.ranking (
                date INTEGER,
                rank INTEGER,
                artist_title TEXT,
                PRIMARY KEY (date, rank),
                FOREIGN KEY(artist_title) REFERENCES cache.song(artist_title)
            );
        ''')

    def song_cached(self, artist_title):
        query = 'SELECT * FROM cache.song WHERE artist_title = ?'
        with self.lock:
            result = self.con.execute(query, [artist_title]).fetchall()
        return len(result) != 0

    def ranking_cached(self, date):
        query = 'SELECT * FROM cache.ranking WHERE date = ?'
        with self.lock:
            result = self.con.execute(query, [date]).fetchall()
        return len(result) != 0

    def cache_songs(self, songs):
        with self.lock:
            cached_songs = self.con.execute('SELECT artist_title FROM cache.song').fetchall()
        songs_to_cache = []

        for song in songs:
            if song not in cached_songs:
                songs_to_cache.append(song)

        if len(songs_to_cache) == 0:
            return

        data = [(
            song['artist_title'],
            song['artist'],
            song['title'],
            song['cover_url'],
            ';'.join(song['genres']),
        ) for song in songs_to_cache]

        query = """
                    INSERT INTO cache.song(artist_title, artist, title, cover_url, genres)
                    VALUES (?, ?, ?, ?, ?)
                    """
        with self.lock:
            self.con.executemany(query, data)

    def cache_ranking(self, ranking, date):
        query = """
                    INSERT INTO cache.ranking(date, rank, artist_title)
                    VALUES (? ,? ,?)
                    """
        data = [(
            date,
            i + 1,
            ranking[i]
        ) for i in range(len(ranking))]
        with self.lock:
            self.con.executemany(query, data)

    def get_ranking(self, date):
        query = """
        SELECT rank, artist, title, cover_url, genres
        FROM cache.ranking r JOIN cache.song s ON r.artist_title = s.artist_title
        WHERE r.date = ?
        ORDER BY rank ASC 
        """
        with self.lock:
            result = [{
                'rank': song[0],
                'artist': song[1],
                'title': song[2],
                'cover_url': song[3],
                'genres': song[4].split(';')
            } for song in self.con.execute(query, [date]).fetchall()]

        return result


class Storage:
    def __init__(self):
        self.selected_date = (pd.Timestamp.now() - pd.Timedelta(days=1)).strftime('%Y%m%d')
        self.top_n = 15
        self.db = Database()

    def get_n_songs_for_date(self, date, top_n):
        if not self.db.ranking_cached(date):
            ranking, songs = self._fetch_data(date)
            self.db.cache_songs(songs)
            self.db.cache_ranking(ranking, date)

        return self.db.get_ranking(date)[:top_n]

    def get_genres_count(self, date):
        result = {}
        for song in self.get_n_songs_for_date(date, self.top_n):
            for genre in song['genres']:
                if genre == '':
                    continue
                if genre not in result.keys():
                    result[genre] = 0
                result.update({genre: result[genre] + 1})

        result = {'values': list(result.values()), 'labels': list(result.keys())}

        return result

    def get_popularity_over_time(self, date, song_rank):
        # Generate date list
        total_days = 30

        date_dt = pd.to_datetime(date, format='%Y%m%d').normalize()
        timestamp_now = pd.Timestamp.now().normalize()

        if (timestamp_now - date_dt).days > 15:
            start_date = date_dt - pd.Timedelta(days=15)
            end_date = date_dt + pd.Timedelta(days=14)
        else:
            days_before = min(15, (date_dt - timestamp_now).days + 14)
            days_after = total_days - days_before - 1

            if days_after < 0:
                days_after = 0

            start_date = date_dt - pd.Timedelta(days=days_before)
            end_date = date_dt + pd.Timedelta(days=days_after)

            if end_date >= timestamp_now:
                end_date = timestamp_now - pd.Timedelta(days=1)
                days_after = (end_date - date_dt).days
                days_before = total_days - days_after - 1
                start_date = date_dt - pd.Timedelta(days=days_before)

        date_list = pd.date_range(start=start_date, end=end_date).tolist()

        # Get the song
        target_song = self.get_n_songs_for_date(date, self.top_n)[song_rank]

        # Prepare data
        result = {}
        for date in date_list:
            songs = self.get_n_songs_for_date(date.strftime("%Y%m%d"), MAX_SONGS)
            for i in range(len(songs)):
                if songs[i]['title'] == target_song['title']:
                    result.update({date.strftime('%Y-%m-%d'): MAX_SONGS - i})
                    break
                else:
                    result.update({date.strftime('%Y-%m-%d'): 0})

        values = list(result.values())
        labels = list(result.keys())

        max_ranking = MAX_SONGS
        tickvals = [max_ranking - i for i in range(max_ranking)]
        ticktext = [i + 1 for i in range(max_ranking)]

        result = {
            'values': values,
            'labels': labels,
            'tickvals': tickvals,
            'ticktext': ticktext
        }

        return result

    def _fetch_data(self, date):
        print(f'Fetching songs for {date}')
        tasks = []
        songs = []
        songs_to_fetch = []

        async def _fetch_song_data(artist_title):
            # Default values (if song not found)
            song = {
                'artist_title': artist_title,
                'artist': artist_title.split('-')[0].strip(),
                'title': artist_title.split('-')[1].strip(),
                'cover_url': 'https://i.pinimg.com/236x/b7/8d/3b/b78d3bd19d5b671b5f1bf779028ad6a8.jpg',
                'genres': []
            }

            # Fetch song info
            search_url = f"https://api.deezer.com/search?q={artist_title}"
            search_response = requests.get(search_url)
            search_data = search_response.json()

            # Nothing came back
            try:
                if not search_data['data']:
                    # Try searching just the title
                    # print(f'"{artist_title}" not found, but not all is lost...')
                    search_url = f"https://api.deezer.com/search?q={artist_title.split('-')[1].strip()}"
                    search_response = requests.get(search_url)
                    search_data = search_response.json()

                    # Nothing came back again :(
                    if not search_data['data']:
                        # print(f'"{artist_title.split("-")[1].strip()}" not found either, all is lost...')
                        return

            except KeyError:
                return

            # Search for correct search result (remixes tend to come up first)
            found = False
            for data in search_data['data']:
                if data['title'] == artist_title.split('-')[1].strip():
                    search_data = data
                    found = True
                    break

            # Well... just get the first result
            if not found:
                # print(f'"{artist_title}" does not match any title in search result, falling back on first result.')
                search_data = search_data['data'][0]

            # Genres data stored in album, so fetch album data
            album_id = search_data['album']['id']
            album_url = f"https://api.deezer.com/album/{album_id}"
            album_response = requests.get(album_url)
            album_data = album_response.json()

            # Get the data
            song['artist'] = search_data['artist']['name']
            song['title'] = search_data['title']
            song['cover_url'] = search_data['album']['cover_big']
            song['genres'] = [genre['name'] for genre in album_data['genres']['data']]
            songs.append(song)

        # Fetch ranking
        url = f'https://kworb.net/eu/archive/{date}.html'
        ranking = pd.read_html(url, attrs={'class': 'sortable'})[0].head(MAX_SONGS)[
            'Artist and Title'].values.tolist()

        # Fetch only songs that are not cached
        for artist_title in ranking:
            if not self.db.song_cached(artist_title):
                songs_to_fetch.append(artist_title)

        async def _fetch_songs():
            for song in songs_to_fetch:
                tasks.append(_fetch_song_data(song))
            await asyncio.gather(*tasks)

        asyncio.run(_fetch_songs())

        return ranking, songs
