from base64 import b64encode
from cloudscraper import create_scraper
from random import choice, random, randrange
from time import sleep
from urllib.parse import quote
from urllib3 import disable_warnings
import json
import os

from bot import config_dict, LOGGER, SHORTENERES, SHORTENER_APIS
from bot.helper.ext_utils.bot_utils import is_premium_user


# Files for persistent counter and URL mappings
COUNTER_FILE = 'counter.txt'
MAPPING_FILE = 'url_mappings.json'


def get_next_counter():
    try:
        with open(COUNTER_FILE, 'r+') as file:
            count = int(file.read())
            count += 1
            file.seek(0)
            file.write(str(count))
            file.truncate()
    except FileNotFoundError:
        count = 1
        with open(COUNTER_FILE, 'w') as file:
            file.write(str(count))
    return count


def load_mappings():
    try:
        with open(MAPPING_FILE, 'r') as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return {}


def save_mappings(mappings):
    with open(MAPPING_FILE, 'w') as f:
        json.dump(mappings, f)


def store_mapping(short_key, long_url):
    mappings = load_mappings()
    mappings[short_key] = long_url
    save_mappings(mappings)


def short_url(longurl, user_id=None, attempt=0):
    def shorte_st():
        headers = {'public-api-token': _shortener_api}
        data = {'urlToShorten': quote(longurl)}
        return cget('PUT', 'https://api.shorte.st/v1/data/url', headers=headers, data=data).json()['shortenedUrl']

    def linkvertise():
        url = quote(b64encode(longurl.encode('utf-8')))
        linkvertise_urls = [f'https://link-to.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}',
                            f'https://up-to-down.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}',
                            f'https://direct-link.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}',
                            f'https://file-link.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}']
        return choice(linkvertise_urls)

    def default_shortener():
        res = cget('GET', f'https://{_shortener}/api?api={_shortener_api}&url={quote(longurl)}').json()
        return res.get('shortenedUrl', longurl)

    shortener_functions = {'shorte.st': shorte_st, 'linkvertise': linkvertise}

    # Check if shortening should be skipped for premium or owner users and config
    if (((not SHORTENERES and not SHORTENER_APIS) or (config_dict['PREMIUM_MODE'] and user_id and is_premium_user(user_id)) or
         user_id == config_dict['OWNER_ID']) and not config_dict['FORCE_SHORTEN']):
        return longurl

    # Try configured shortener APIs first
    for _ in range(4 - attempt):
        i = 0 if len(SHORTENERES) == 1 else randrange(len(SHORTENERES))
        _shortener = SHORTENERES[i].strip()
        _shortener_api = SHORTENER_APIS[i].strip()
        cget = create_scraper().request
        disable_warnings()
        try:
            for key in shortener_functions:
                if key in _shortener:
                    return shortener_functions[key]()
            return default_shortener()
        except Exception as e:
            LOGGER.error(e)
            sleep(1)

    # If all API shorteners fail, fallback to incremental counting short URL
    count = get_next_counter()
    short_key = f"DCBOTS______{count}"
    store_mapping(short_key, longurl)

    # Build your Heroku or custom redirect base URL here:
    base_url = config_dict.get('SHORTENER_BASE_URL', 'https://your-app-name.herokuapp.com')

    short_url_final = f"{base_url}/{short_key}"
    return short_url_final
