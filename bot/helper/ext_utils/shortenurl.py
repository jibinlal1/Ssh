import string
import random
from base64 import b64encode
from cloudscraper import create_scraper
from random import choice, randrange
from time import sleep
from urllib.parse import quote
from urllib3 import disable_warnings

from bot import config_dict, LOGGER, SHORTENERES, SHORTENER_APIS
from bot.helper.ext_utils.bot_utils import is_premium_user

def generate_alias(prefix="DCBOTS______fixing______", length=8):
    chars = string.ascii_letters + string.digits
    suffix = ''.join(random.choices(chars, k=length))
    return prefix + suffix

def short_url(longurl, user_id=None, attempt=0, use_custom_alias=True):
    def shorte_st():
        headers = {'public-api-token': _shortener_api}
        data = {'urlToShorten': quote(longurl)}
        return cget('PUT', 'https://api.shorte.st/v1/data/url', headers=headers, data=data).json().get('shortenedUrl', longurl)

    def gplinks_shortener():
        params = f'api={_shortener_api}&url={quote(longurl)}'
        if use_custom_alias:
            custom_alias = generate_alias()
            params += f'&alias={quote(custom_alias)}'
        res = cget('GET', f'https://api.gplinks.com/api?{params}').json()
        LOGGER.info(f"gplinks: {res}")
        return res.get('shortenedUrl', longurl)

    def linkvertise():
        url = quote(b64encode(longurl.encode('utf-8')))
        linkvertise_urls = [
            f'https://link-to.net/{_shortener_api}/{random.random() * 1000}/dynamic?r={url}',
            f'https://up-to-down.net/{_shortener_api}/{random.random() * 1000}/dynamic?r={url}',
            f'https://direct-link.net/{_shortener_api}/{random.random() * 1000}/dynamic?r={url}',
            f'https://file-link.net/{_shortener_api}/{random.random() * 1000}/dynamic?r={url}'
        ]
        return choice(linkvertise_urls)

    def default_shortener():
        params = f'api={_shortener_api}&url={quote(longurl)}'
        res = cget('GET', f'https://{_shortener}/api?{params}').json()
        return res.get('shortenedUrl', longurl)

    shortener_functions = {
        'shorte.st': shorte_st,
        'linkvertise': linkvertise,
        'gplinks': gplinks_shortener
    }

    if (
        (not SHORTENERES and not SHORTENER_APIS)
        or (config_dict.get('PREMIUM_MODE') and user_id and is_premium_user(user_id))
        or user_id == config_dict.get('OWNER_ID')
    ) and not config_dict.get('FORCE_SHORTEN'):
        return longurl

    for _ in range(4 - attempt):
        i = 0 if len(SHORTENERES) == 1 else randrange(len(SHORTENERES))
        _shortener = SHORTENERES[i].strip()
        _shortener_api = SHORTENER_APIS[i].strip()
        cget = create_scraper().request
        disable_warnings()
        try:
            for key, fn in shortener_functions.items():
                if key in _shortener:
                    return fn()
            return default_shortener()
        except Exception as e:
            LOGGER.error(f"Error using {_shortener}: {e}")
            sleep(1)
    return longurl
