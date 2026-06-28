import os
import re
import sys
import json
import time
import random
import shutil
import base64
from datetime import datetime, timedelta
import pytz
from collections import OrderedDict
from urllib.parse import urlparse, urljoin
import cloudscraper

# Target API configuration
BASE_URL = os.getenv("BASE_URL")
API_URL = "https://backend.streamcenter.live/api/Parties?pageNumber=10&pageSize=500"
OUTPUT_FILE = "Stream-Live.json"

def get_ist_time():
    ist = pytz.timezone('Asia/Kolkata')
    return datetime.now(ist).strftime('%d/%m/%y %H:%M:%S IST')

def log_to_console(message):
    """Prints logs to sys.stderr so they appear in GitHub Actions but do not pollute the raw JSON output."""
    print(message, file=sys.stderr)

def deduplicate(seq):
    """Helper function to remove duplicates while preserving order."""
    seen = set()
    return [x for x in seq if not (x in seen or seen.add(x))]

def push_to_github():
    GITHUB_TOKEN = os.getenv("GH_TOKEN")
    GITHUB_USER = os.getenv("TGITHUB_USER")
    GITHUB_REPO = os.getenv("TGITHUB_REPO")
    GITHUB_EMAIL = os.getenv("TGITHUB_EMAIL")
    
    if not GITHUB_TOKEN or not GITHUB_USER or not GITHUB_REPO:
        log_to_console("[ERROR] GitHub secrets are missing. Skipping push.")
        return

    temp_dir = "temp_external_repo"
    remote_url = f"https://{GITHUB_TOKEN}@github.com/{GITHUB_USER}/{GITHUB_REPO}.git"

    try:
        if os.path.exists(temp_dir):
            shutil.rmtree(temp_dir)
            
        clone_status = os.system(f"git clone {remote_url} {temp_dir}")
        if clone_status != 0:
            raise Exception("Git Clone failed. Please check your token or repo permissions.")
        
        shutil.copy(OUTPUT_FILE, os.path.join(temp_dir, OUTPUT_FILE))
        
        current_dir = os.getcwd()
        os.chdir(temp_dir)
        
        os.system(f'git config user.email "{GITHUB_EMAIL if GITHUB_EMAIL else "action@github.com"}"')
        os.system(f'git config user.name "{GITHUB_USER}"')
        os.system(f"git add {OUTPUT_FILE}")
        os.system(f'git commit -m "Auto Update: {get_ist_time()}" || echo "No changes"')
        push_status = os.system("git push origin main")
        
        os.chdir(current_dir)
        shutil.rmtree(temp_dir)
        
        if push_status == 0:
            log_to_console(f"[SUCCESS] {OUTPUT_FILE} successfully updated in {GITHUB_USER}/{GITHUB_REPO}.")
        else:
            log_to_console("[ERROR] Git push command failed.")
            
    except Exception as e:
        log_to_console(f"[ERROR] Push failed: {e}")

def extract_stream_token(scraper, player_url):
    """Fetches player page and extracts raw .m3u8/.ts streams or unique token IDs."""
    try:
        res = scraper.get(player_url, timeout=10)
        res.encoding = 'utf-8'  # Enforce proper UTF-8 decoding [cite: 2.1]
        html = res.text
        
        # 1. Search for unique stream tokens directly
        stream_ids = re.findall(r'stream=([a-zA-Z0-9_.-]+)', html)
        if stream_ids:
            return deduplicate(stream_ids)
            
        # 2. Search for direct .m3u8/.ts stream URLs
        direct_urls = re.findall(r'(https?://[^\s"\'<>]+(?:\.m3u8|\.ts)[^\s"\'<>/]*)', html)
        if direct_urls:
            return deduplicate(direct_urls)

        # 3. Base64 decoded check (For obfuscated sources)
        b64_blocks = re.findall(r'[a-zA-Z0-9+/=]{24,}', html)
        for block in b64_blocks:
            try:
                padded = block + "=" * ((4 - len(block) % 4) % 4)
                decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
                if "http" in decoded and (".m3u8" in decoded or ".ts" in decoded):
                    extracted = re.findall(r'(https?://[^\s"\'<>]+(?:\.m3u8|\.ts)[^\s"\'<>/]*)', decoded)
                    if extracted:
                        return deduplicate(extracted)
            except Exception:
                continue

        # 4. Scan embedded iframe recursively
        iframe_matches = re.findall(r'<iframe[^>]+src=["\']([^"\']+)["\']', html, re.I)
        for iframe_url in iframe_matches:
            if not iframe_url.startswith('http'):
                if iframe_url.startswith('//'):
                    iframe_url = 'https:' + iframe_url
                else:
                    iframe_url = urljoin(player_url, iframe_url)
            
            iframe_url = iframe_url.replace("streams.center", "streamcenter.xyz")
            time.sleep(random.uniform(0.3, 0.6))
            
            iframe_res = scraper.get(iframe_url, timeout=10)
            iframe_res.encoding = 'utf-8'  # Enforce UTF-8 [cite: 2.1]
            iframe_html = iframe_res.text
            
            inner_stream_ids = re.findall(r'stream=([a-zA-Z0-9_.-]+)', iframe_html)
            if inner_stream_ids:
                return deduplicate(inner_stream_ids)
                
            inner_direct = re.findall(r'(https?://[^\s"\'<>]+(?:\.m3u8|\.ts)[^\s"\'<>/]*)', iframe_html)
            if inner_direct:
                return deduplicate(inner_direct)
                
            b64_inner = re.findall(r'[a-zA-Z0-9+/=]{24,}', iframe_html)
            for block in b64_inner:
                try:
                    padded = block + "=" * ((4 - len(block) % 4) % 4)
                    decoded = base64.b64decode(padded).decode('utf-8', errors='ignore')
                    if "http" in decoded and (".m3u8" in decoded or ".ts" in decoded):
                        extracted = re.findall(r'(https?://[^\s"\'<>]+(?:\.m3u8|\.ts)[^\s"\'<>/]*)', decoded)
                        if extracted:
                            return deduplicate(extracted)
                except Exception:
                    continue
                    
    except Exception as e:
        log_to_console(f"    [!] Error during token extraction: {str(e)}")
    return []

def run_scraper():
    # Verify if BASE_URL secret is provided
    if not BASE_URL:
        error_package = OrderedDict([
            ("Owner", "Ivan-FluX"),
            ("App name", "Stream-Live"),
            ("Status", "Failed"),
            ("Error", "BASE_URL environment variable is missing. Please add BASE_URL to GitHub Secrets.")
        ])
        print(json.dumps(error_package, indent=4, ensure_ascii=False))
        return

    # Use backend API URL
    api_endpoint = "https://backend.streamcenter.live/api"

    scraper = cloudscraper.create_scraper(browser={'browser': 'chrome', 'platform': 'android', 'desktop': False})
    raw_matches = []
    
    log_to_console(f"[*] Loading homepage: {BASE_URL}")
    try:
        res = scraper.get(f"{BASE_URL}", timeout=15)
        res.encoding = 'utf-8'  # Force UTF-8 [cite: 2.1]
        homepage_html = res.text
        log_to_console("[+] Homepage loaded successfully.")
    except Exception as e:
        error_package = OrderedDict([
            ("Owner", "Ivan-FluX"),
            ("App name", "Stream-Live"),
            ("Status", "Failed"),
            ("Error", "Could not connect to the website. Possibly blocked by Cloudflare or network timeout."),
            ("Details", str(e))
        ])
        print(json.dumps(error_package, indent=4, ensure_ascii=False))
        return

    # Fetching Categories Map directly from API
    categories_map = {}
    log_to_console("[*] Loading categories from API...")
    try:
        cats_res = scraper.get(f"{api_endpoint}/Categories", timeout=10)
        cats_res.encoding = 'utf-8'
        if cats_res.status_code == 200:
            categories = cats_res.json()
            for cat in categories:
                categories_map[cat.get("id")] = cat.get("name", "General")
            log_to_console(f"[+] Loaded {len(categories_map)} categories successfully.")
    except Exception as e:
        log_to_console(f"[WARNING] Categories load failed: {e}")

    # Fetching Active Matches from exact target API [1]
    log_to_console(f"\n[*] Loading matches from API: {API_URL}")
    try:
        games_res = scraper.get(API_URL, timeout=15)
        games_res.encoding = 'utf-8'
        if games_res.status_code != 200:
            log_to_console(f"[ERROR] API failed with status: {games_res.status_code}")
            return
        games = games_res.json()
    except Exception as e:
        log_to_console(f"[ERROR] API connection failed: {e}")
        return

    log_to_console(f"[+] Found {len(games)} total scheduled matches.")
    log_to_console("-" * 50)

    # Filter active and upcoming matches across all categories
    for idx, game in enumerate(games, 1):
        game_name = (game.get("name", "")).replace(" | ", " vs ").strip()
        cat_id = game.get("categoryId")
        cat_name = categories_map.get(cat_id, "General")
        game_id = game.get("id")
        
        # Skip finished/ended matches based on start time (if started more than 4 hours ago)
        begin_time_str = game.get("beginPartie") or game.get("date")
        if begin_time_str:
            try:
                if begin_time_str.endswith('Z'):
                    begin_time_str = begin_time_str[:-1] + '+00:00'
                match_dt = datetime.fromisoformat(begin_time_str)
                now_utc = datetime.now(pytz.utc)
                if match_dt + timedelta(hours=4) < now_utc:
                    continue  # Skip ended matches
            except Exception:
                pass
                
        # Locate player/embed URLs for this match
        player_urls = []
        
        # 1. Parse from videoUrl
        video_url_field = game.get("videoUrl")
        if video_url_field and isinstance(video_url_field, str):
            parts = video_url_field.split(";")
            for part in parts:
                url = part.split("<")[0].strip() if "<" in part else part.strip()
                if url:
                    url = url.replace("streams.center", "streamcenter.xyz")
                    player_urls.append(url)
                    
        # 2. Check stream arrays in object
        for key in ["streams", "servers"]:
            if key in game and isinstance(game[key], list):
                for s in game[key]:
                    url = s.get("url") or s.get("stream")
                    if url:
                        url = url.replace("streams.center", "streamcenter.xyz")
                        player_urls.append(url)

        # 3. Fetch fallback Parties/{id}/Servers endpoint
        try:
            srv_res = scraper.get(f"{api_endpoint}/Parties/{game_id}/Servers", timeout=10)
            srv_res.encoding = 'utf-8'
            if srv_res.status_code == 200:
                srv_data = srv_res.json()
                if isinstance(srv_data, list):
                    for s in srv_data:
                        url = s.get("url") or s.get("stream")
                        if url:
                            url = url.replace("streams.center", "streamcenter.xyz")
                            player_urls.append(url)
        except Exception:
            pass

        player_urls = deduplicate(player_urls)
        
        if not player_urls:
            continue
            
        raw_matches.append({
            "cat_name": cat_name,
            "clean_rivals": game_name,
            "player_urls": player_urls
        })

    # Output generation
    all_live_matches = []
    log_to_console(f"\n[*] Extracting unique tokens for {len(raw_matches)} filtered matches...")
    log_to_console("-" * 50)
    
    for item in raw_matches:
        log_to_console(f"[*] Match: {item['clean_rivals']}...")
        
        for s_idx, p_url in enumerate(item["player_urls"], 1):
            time.sleep(random.uniform(0.5, 1.0))
            
            # Extract unique token (e.g., eJmauBDCIf)
            extracted_items = extract_stream_token(scraper, p_url)
            
            if extracted_items:
                for stream_item in extracted_items:
                    # Construct the final streaming link depending on type [1]
                    if not stream_item.startswith("http") and len(stream_item) < 40:
                        final_link = f"https://mainstreams.pro/hls/{stream_item}.m3u8|Referer=https://streamcenter.xyz/"
                    else:
                        # Direct stream fallback
                        final_link = f"{stream_item}|Referer=https://streamcenter.xyz/"
                        
                    log_to_console(f"      >>> [SUCCESS] Link Created (S-{s_idx}): {final_link}")
                    
                    all_live_matches.append(OrderedDict([
                        ("Id", str(len(all_live_matches) + 1)),
                        ("Rivels", item["clean_rivals"]),
                        ("Title", f"{item['cat_name']} (S-{s_idx})"),
                        ("Link", final_link)
                    ]))
            else:
                log_to_console(f"      >>> [FAILED] Could not find stream token from Server {s_idx}")

    # Structure final JSON package
    final_package = OrderedDict([
        ("Owner", "Ivan-FluX"),
        ("App name", "Stream-Live"),
        ("Last update", get_ist_time()),
        ("Total_Matches", len(all_live_matches)),
        ("Live_Data", all_live_matches)
    ])
    
    # Save output inside the Action runner using explicit UTF-8 encoding
    with open(OUTPUT_FILE, "w", encoding="utf-8") as f:
        json.dump(final_package, f, indent=4, ensure_ascii=False)
    
    # Push to target repository
    push_to_github()
    
    # Print raw formatted JSON output to standard output only
    print(json.dumps(final_package, indent=4, ensure_ascii=False))

if __name__ == "__main__":
    run_scraper()
