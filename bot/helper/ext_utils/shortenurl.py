import string
import random
import requests
from bs4 import BeautifulSoup

# ========== CONFIG ===============
SHORTENER_API_KEY = "e71e7d279ec4e3dd4fe6dff6779a78ec981dc954"
SHORTENER_DOMAIN = "gplinks.com"

def generate_alias(prefix="DCBOTS_________", length=5):
    chars = string.ascii_letters + string.digits
    suffix = ''.join(random.choices(chars, k=length))
    return prefix + suffix

def create_gplinks_shortlink(longurl, use_custom_alias=True):
    params = {
        'api': SHORTENER_API_KEY,
        'url': longurl
    }
    if use_custom_alias:
        custom_alias = generate_alias()
        params['alias'] = custom_alias
    response = requests.get(f'https://api.{SHORTENER_DOMAIN}/api', params=params)
    data = response.json()
    # Fallback to long URL if failed
    return data.get('shortenedUrl', longurl), params.get('alias', '')

def check_link_clicked(alias):
    """Scrape the info page to check if there was at least 1 click."""
    info_url = f'https://{SHORTENER_DOMAIN}/{alias}/info'
    r = requests.get(info_url)
    soup = BeautifulSoup(r.text, 'html.parser')
    # This depends on the actual HTML structure; 
    # here, we search for 'Clicks' and fetch the integer,
    # adjust selector as needed for your info page
    try:
        table_cells = soup.find_all('td')
        for idx, td in enumerate(table_cells):
            if 'Clicks' in td.get_text():
                return int(table_cells[idx+1].get_text().strip())
        return 0
    except Exception as e:
        print("Parsing error:", e)
        return 0

def send_alert_message():
    alert = ("⚠️ Using bots, adblockers, or DNS services to bypass the shortener "
             "is strictly prohibited and will lead to a ban.")
    print(alert)
    # Use this wherever you need to send/display the alert.

# ========== EXAMPLE USAGE ================
if __name__ == "__main__":
    long_url = "https://example.com/my-very-long-link"
    
    # Create short link
    shortlink, alias = create_gplinks_shortlink(long_url)
    print("Shortened URL:", shortlink)
    print("Alias used:", alias)
    
    # --- ALERT SECTION ---
    send_alert_message()
    
    # --- USER ACTION: Wait for a real user to click ---
    input("Press Enter after the user claims to have clicked the link...")

    # --- CHECK IF LINK HAS BEEN USED ---
    clicks = check_link_clicked(alias)
    if clicks > 0:
        print(f"✅ Link has been clicked {clicks} times. Proceed!")
    else:
        print("🚫 Link has not been clicked yet. Action denied!")

