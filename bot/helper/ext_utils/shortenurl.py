import re
from base64 import b64encode
from cloudscraper import create_scraper
from random import choice, random, randrange
from time import sleep
from urllib.parse import quote
from urllib3 import disable_warnings

from bot import config_dict, LOGGER, SHORTENERES, SHORTENER_APIS
from bot.helper.ext_utils.bot_utils import is_premium_user

def validate_custom_alias(alias):
    # Validate alias with prefix DCBOTS______ followed by alphanumeric/underscore/dash
    pattern = r'^DCBOTS________[a-zA-Z0-9_-]+$'
    if alias and re.match(pattern, alias) and len(alias) <= 30:
        return True
    return False

def short_url(longurl, user_id=None, attempt=0, custom_alias=None):
    def shorte_st():
        headers = {'public-api-token': _shortener_api}
        data = {'urlToShorten': quote(longurl)}
        if custom_alias and validate_custom_alias(custom_alias):
            data['customAlias'] = custom_alias
        return cget('PUT', 'https://api.shorte.st/v1/data/url', headers=headers, data=data).json().get('shortenedUrl', longurl)

    def gplinks_shortener():
        params = f'api={_shortener_api}&url={quote(longurl)}'
        if custom_alias and validate_custom_alias(custom_alias):
            params += f'&alias={quote(custom_alias)}'
        res = cget('GET', f'https://api.gplinks.com/api?{params}').json()
        return res.get('shortenedUrl', longurl)

    def linkvertise():
        url = quote(b64encode(longurl.encode('utf-8')))
        linkvertise_urls = [
            f'https://link-to.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}',
            f'https://up-to-down.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}',
            f'https://direct-link.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}',
            f'https://file-link.net/{_shortener_api}/{random() * 1000}/dynamic?r={url}'
        ]
        return choice(linkvertise_urls)

    def default_shortener():
        params = f'api={_shortener_api}&url={quote(longurl)}'
        if custom_alias and validate_custom_alias(custom_alias):
            params += f'&alias={quote(custom_alias)}'
        res = cget('GET', f'https://{_shortener}/api?{params}').json()
        return res.get('shortenedUrl', longurl)

    if (((not SHORTENERES and not SHORTENER_APIS) or (config_dict['PREMIUM_MODE'] and user_id and is_premium_user(user_id)) or
         user_id == config_dict['OWNER_ID']) and not config_dict['FORCE_SHORTEN']):
        # Bypass shortening for premium or owner or config
        return longurl

    for _ in range(4 - attempt):
        i = 0 if len(SHORTENERES) == 1 else randrange(len(SHORTENERES))
        _shortener = SHORTENERES[i].strip()
        _shortener_api = SHORTENER_APIS[i].strip()
        cget = create_scraper().request
        disable_warnings()
        try:
            shortener_functions = {
                'shorte.st': shorte_st,
                'gplinks': gplinks_shortener,
                'linkvertise': linkvertise
            }
            for key in shortener_functions:
                if key in _shortener:
                    return shortener_functions[key]()
            return default_shortener()
        except Exception as e:
            LOGGER.error(f"Shortening attempt failed for {_shortener} with error: {e}")
            sleep(1)

    # Fallback to original URL if all attempts fail
    return longurl
