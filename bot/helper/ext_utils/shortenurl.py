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

# == new imports for info check ==
import requests
from bs4 import BeautifulSoup

def generate_alias(prefix="DCBOTS_________", length=5):
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

# =========================
#     NEW FEATURE BELOW
# =========================

def get_gplinks_info(alias):
    """
    Check click statistics of a gplinks shortlink by alias.
    Returns: {
        "country_clicks": dict,
        "total_clicks": int,
        "referrers": dict
    }
    """
    info_url = f"https://gplinks.com/{alias}/info"
    try:
        r = requests.get(info_url, timeout=10)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, 'html.parser')
        # Country clicks table
        country_clicks = {}
        total_clicks = 0
        referrers = {}

        # Get country and click info
        countries = [td.get_text(strip=True) for td in soup.find_all('td')]
        for idx, v in enumerate(countries):
            if v == "Country":
                # Next cell is country, then next is clicks value, etc.
                try:
                    country = countries[idx+1]
                    clicks = int(countries[idx+2])
                    country_clicks[country] = clicks
                    total_clicks += clicks
                except:
                    pass

        # Fallback to sum first detected country clicks table
        if not country_clicks:
            # Attempt to find first number as clicks
            for td in soup.find_all('td'):
                text = td.get_text(strip=True)
                if text.isdigit():
                    total_clicks = int(text)
                    break
        # Referrer parsing
        for idx, v in enumerate(countries):
            if v == "Domain":
                domain = countries[idx+1]
                clicks = int(countries[idx+2])
                referrers[domain] = clicks

        return {
            "country_clicks": country_clicks,
            "total_clicks": total_clicks,
            "referrers": referrers
        }
    except Exception as e:
        LOGGER.error(f"Info page parse failed for alias {alias}: {e}")
        return {
            "country_clicks": {},
            "total_clicks": 0,
            "referrers": {}
        }

# Helper to check if link was used
def gplinks_link_clicked(alias, min_clicks=1):
    """
    Returns True if the gplinks alias has at least `min_clicks` clicks, else False.
    """
    try:
        info = get_gplinks_info(alias)
        return info["total_clicks"] >= min_clicks
    except:
        return False

# ============= END =============

