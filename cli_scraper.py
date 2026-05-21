import os
import json
import time
import re
import sys
import requests
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# --- PATHS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "extracted_data")
STATUS_FILE = os.path.join(BASE_DIR, "current_status.json")
EXTRACTION_LOG_FILE = os.path.join(BASE_DIR, "extraction_log.json")

os.makedirs(DATA_DIR, exist_ok=True)

# --- CONFIG ---
API_URL = "https://anilit.mangalit.org/ad/upload_api.php"
API_KEY = os.getenv("ANILIT_API_KEY")

# --- UTILS ---
def add_log(text):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{timestamp} {text}", flush=True)

def escape_sql(text):
    if text is None: return "NULL"
    return "'" + str(text).replace("'", "''").replace("\\", "\\\\") + "'"

def slugify(text):
    if not text: return "n-a"
    text = text.lower()
    
    # 1. Replace special season/edition markers with word equivalents
    text = text.replace('½', 'half')
    text = text.replace('¼', 'quarter')
    text = text.replace('#', 'sharp')
    text = text.replace('+', 'plus')
    text = text.replace('*', 'star')
    
    # 2. Replace punctuation markers anywhere in the title to preserve distinct seasons/OVAs
    text = text.replace('!!', '-two')
    text = text.replace('!', '-one')
    text = text.replace('??', '-two')
    text = text.replace('?', '-one')
    text = text.replace('.', '-dot')
    text = text.replace(',', '-comma')
    text = text.replace(':', '-colon')
    text = text.replace("'", '-prime')
    
    # 3. Clean and return
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

def get_driver():
    options = Options()
    options.add_argument("--incognito")
    options.add_argument("--headless=new")
    options.add_argument("--disable-gpu")
    options.add_argument("--window-size=1920,1080")
    options.add_argument("--mute-audio")
    options.add_argument("--no-sandbox")
    options.add_argument("--disable-dev-shm-usage")
    options.add_argument("--disable-blink-features=AutomationControlled")
    options.add_argument("user-agent=Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36")

    # Linux / GitHub Actions runner detection
    if sys.platform.startswith("linux"):
        add_log("[*] Linux environment detected. Launching system Google Chrome.")
        options.binary_location = "/usr/bin/google-chrome-stable"
        service = Service(ChromeDriverManager().install())
        return webdriver.Chrome(service=service, options=options)
    
    # Fallback to local setup (Windows/Mac)
    if sys.platform.startswith("win"):
        opera_paths = [
            os.path.expanduser(r"~\AppData\Local\Programs\Opera GX\launcher.exe"),
            os.path.expanduser(r"~\AppData\Local\Programs\Opera\launcher.exe"),
            r"C:\Program Files\Opera\launcher.exe",
            r"C:\Program Files (x86)\Opera\launcher.exe",
            r"C:\Program Files\Opera GX\launcher.exe",
            r"C:\Program Files (x86)\Opera GX\launcher.exe"
        ]
        OPERA_PATH = next((p for p in opera_paths if os.path.exists(p)), None)
        if OPERA_PATH:
            add_log(f"[*] Found Opera at: {OPERA_PATH}")
            options.binary_location = OPERA_PATH
            options.add_argument("--private")
            service = Service(ChromeDriverManager().install())
            return webdriver.Chrome(service=service, options=options)

    add_log("[*] Local Chrome environment detected. Launching Chrome.")
    service = Service(ChromeDriverManager().install())
    return webdriver.Chrome(service=service, options=options)

def save_to_json(data, filename):
    try:
        existing_data = {}
        if os.path.exists(filename):
            try:
                with open(filename, "r", encoding="utf-8") as f:
                    existing_data = json.load(f)
            except: pass
        
        # Update metadata
        for key in ['title', 'slug', 'description', 'genres', 'studio', 'cover_image', 'background_image', 'release_date', 'status', 'total_episodes']:
            if key in data:
                existing_data[key] = data[key]
        
        # Update episodes
        if "episodes" not in existing_data:
            existing_data["episodes"] = []
        
        new_episodes = data.get("episodes", [])
        for new_ep in new_episodes:
            found = False
            for i, old_ep in enumerate(existing_data["episodes"]):
                if old_ep["episode_number"] == new_ep["episode_number"]:
                    existing_data["episodes"][i] = new_ep
                    found = True
                    break
            if not found:
                existing_data["episodes"].append(new_ep)
        
        # Sort episodes
        existing_data["episodes"].sort(key=lambda x: x.get("episode_number", 0))
        
        with open(filename, "w", encoding="utf-8") as f:
            json.dump(existing_data, f, indent=4)
    except Exception as e:
        add_log(f"[!] Error saving JSON: {e}")

def generate_sql(data, new_episodes):
    title = data.get('title') or data.get('name', 'Unknown')
    slug = slugify(title)
    cover = data.get('cover_image') or data.get('poster_url', '808080.png')
    category = ", ".join(data.get('genres', [])) or "Anime"
    status = data.get('status', 'ongoing')
    studio = data.get('studio', 'N/A')
    release_date = data.get('release_date', '2024-01-01')
    description = data.get('description', '')
    background_image = data.get('background_image', '')
    total_episodes = data.get('total_episodes', 0)
    
    lines = []
    lines.append(f"-- SQL Export for {title} (Updates)")
    lines.append("SET NAMES utf8mb4;")
    
    # 1. Insert/Update Anime
    anime_sql = f"INSERT INTO animes (title, slug, cover_image, studio, category, release_date, description, status, background_image, views, total_episodes) " \
                f"VALUES ({escape_sql(title)}, {escape_sql(slug)}, {escape_sql(cover)}, " \
                f"{escape_sql(studio)}, {escape_sql(category)}, " \
                f"{escape_sql(release_date)}, {escape_sql(description)}, {escape_sql(status)}, " \
                f"{escape_sql(background_image)}, 0, {total_episodes}) " \
                f"ON DUPLICATE KEY UPDATE id=LAST_INSERT_ID(id), description=VALUES(description), status=VALUES(status), total_episodes=VALUES(total_episodes);"
    lines.append(anime_sql)
    lines.append(f"SET @anime_id = (SELECT id FROM animes WHERE slug = {escape_sql(slug)} LIMIT 1);")
    
    # 2. Episodes and Sources
    for ep in new_episodes:
        ep_num = ep['episode_number']
        ep_title = ep.get('episode_title', f"Episode {ep_num}")
        
        lines.append(f"\n-- Episode {ep_num}")
        lines.append(f"INSERT INTO episodes (anime_id, episode_number, episode_title, is_published) " \
                     f"SELECT @anime_id, {ep_num}, {escape_sql(ep_title)}, 1 " \
                     f"WHERE NOT EXISTS (SELECT 1 FROM episodes WHERE anime_id = @anime_id AND episode_number = {ep_num});")
        
        lines.append(f"SET @episode_id = (SELECT id FROM episodes WHERE anime_id = @anime_id AND episode_number = {ep_num} LIMIT 1);")
        
        for quality, url in ep.get('resolutions', {}).items():
            lines.append(f"INSERT INTO episode_sources (episode_id, source_url, quality) " \
                         f"SELECT @episode_id, {escape_sql(url)}, {escape_sql(quality)} " \
                         f"WHERE NOT EXISTS (SELECT 1 FROM episode_sources WHERE episode_id = @episode_id AND quality = {escape_sql(quality)});")
    
    lines.append("\nCOMMIT;")
    return "\n".join(lines)

def upload_sql_via_api(sql_content):
    try:
        r = requests.post(API_URL, data={
            'action': 'multi_query',
            'sql': sql_content,
            'api_key': API_KEY
        }, timeout=45)
        if r.status_code == 200:
            res = r.json()
            if res.get('success'):
                return True, ""
            else:
                return False, f"API Error: {res.get('error')}"
        else:
            return False, f"HTTP Error {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"Connection Error: {e}"

# --- MAIN LOOP ---
def main():
    add_log("[+] Scraper CLI Started.")
    
    BASE_URL = "https://animepahe.pw"
    driver = None
    
    try:
        driver = get_driver()
        wait = WebDriverWait(driver, 15)
        
        # Load progress status
        extraction_log = {}
        if os.path.exists(EXTRACTION_LOG_FILE):
            try:
                with open(EXTRACTION_LOG_FILE, "r", encoding="utf-8") as f:
                    extraction_log = json.load(f)
            except: pass

        # Scan Page 1 & 2 for updates
        pages_to_scan = [1, 2]
        processed_sessions = {}  # session -> {'title': title, 'episodes': set()}
        all_anime_to_process = [] # list of {"title": title, "session": session}
        
        for current_page in pages_to_scan:
            add_log(f"[*] Scanning Page {current_page}...")
            
            # Save status for monitoring
            try:
                with open(STATUS_FILE, "w", encoding="utf-8") as f:
                    json.dump({
                        "current_page": current_page, 
                        "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                    }, f, indent=4)
            except: pass

            driver.get(f"{BASE_URL}/?page={current_page}")
            time.sleep(3)
            
            # Check for DDoS-Guard
            if "DDoS-Guard" in driver.page_source:
                add_log("[*] DDoS-Guard challenge detected. Waiting 10 seconds...")
                time.sleep(10)
            
            try:
                wraps = driver.find_elements(By.CSS_SELECTOR, ".episode-wrap")
                if not wraps:
                    wraps = driver.find_elements(By.CSS_SELECTOR, ".episode-title-wrap")
            except Exception as e:
                add_log(f"[!] Error getting items: {e}")
                wraps = []

            for wrap in wraps:
                try:
                    links = wrap.find_elements(By.TAG_NAME, "a")
                    if len(links) < 2:
                        continue
                    play_link = links[0]
                    anime_link = links[1]
                    
                    play_text = play_link.text
                    title = anime_link.get_attribute("title") or anime_link.text
                    href = anime_link.get_attribute("href")
                    
                    if title and href and "/anime/" in href:
                        session = href.split("/")[-1]
                        
                        # Match play_text to extract episode number
                        match = re.search(r'-\s*(\d+(?:\.\d+)?)\s+Online$', play_text)
                        if match:
                            ep_num_str = match.group(1)
                            ep_num = float(ep_num_str) if '.' in ep_num_str else int(ep_num_str)
                            
                            if session not in processed_sessions:
                                processed_sessions[session] = {
                                    "title": title,
                                    "episodes": set()
                                }
                                all_anime_to_process.append({"title": title, "session": session})
                            
                            processed_sessions[session]["episodes"].add(ep_num)
                except Exception as e:
                    add_log(f"[!] Error parsing item: {e}")
        
        if not all_anime_to_process:
            add_log("[*] No anime found to process on scanned pages.")
        else:
            # Process in order (oldest first from the bottom of the page/scanned results)
            all_anime_to_process.reverse()
            
            for i, anime in enumerate(all_anime_to_process):
                title = anime['title']
                session = anime['session']
                
                # Check optimization: Can we skip?
                # If it's in the log AND the local metadata JSON shows the status is completed,
                # then it has finished airing and we have fully extracted it. We can skip checking!
                safe_name = re.sub(r'[^a-zA-Z0-9]', '_', title)
                json_filename = os.path.join(DATA_DIR, f"{safe_name}.json")
                
                is_completed_airing = False
                if os.path.exists(json_filename):
                    try:
                        with open(json_filename, "r", encoding="utf-8") as f:
                            local_data = json.load(f)
                            if local_data.get("status") == "completed":
                                is_completed_airing = True
                    except: pass
                
                if title in extraction_log and is_completed_airing:
                    add_log(f"[*] Skipping {title} (Airing completed & fully extracted)")
                    continue
                
                add_log(f"\n=======================================================")
                add_log(f"[*] Processing Anime ({i+1}/{len(all_anime_to_process)}): {title}")
                add_log(f"=======================================================")
                
                # Load existing data to resume/merge at episode level
                completed_episodes_dict = {}
                local_data = {}
                if os.path.exists(json_filename):
                    try:
                        with open(json_filename, "r", encoding="utf-8") as f:
                            local_data = json.load(f)
                            if "episodes" in local_data:
                                for e in local_data["episodes"]:
                                    if e.get("resolutions"):
                                        completed_episodes_dict[e["episode_number"]] = e
                    except: pass
                
                # Fetch Details Page to ensure metadata is fresh and get any info we don't have
                driver.get(f"{BASE_URL}/anime/{session}")
                time.sleep(2.5)
                if "DDoS-Guard" in driver.page_source:
                    add_log("[*] DDoS-Guard challenge on anime page. Waiting 10 seconds...")
                    time.sleep(10)
                
                soup = BeautifulSoup(driver.page_source, "html.parser")
                
                details = {
                    "title": title,
                    "slug": slugify(title),
                    "description": local_data.get("description", "N/A"),
                    "genres": local_data.get("genres", []),
                    "studio": local_data.get("studio", "N/A"),
                    "cover_image": local_data.get("cover_image", "N/A"),
                    "background_image": local_data.get("background_image", "N/A"),
                    "release_date": local_data.get("release_date", "N/A"),
                    "status": local_data.get("status", "ongoing"),
                    "total_episodes": local_data.get("total_episodes", 0),
                    "episodes": []
                }
                
                # Update details if element exists
                desc_elem = soup.select_one(".anime-synopsis")
                if desc_elem:
                    details["description"] = desc_elem.get_text(strip=True)
                    
                genre_elems = soup.select(".anime-genre ul li a")
                if genre_elems:
                    details["genres"] = [g.get_text(strip=True) for g in genre_elems]
                    
                info_div = soup.select_one(".anime-info")
                if info_div:
                    for p in info_div.select("p"):
                        text = p.get_text(strip=True)
                        if "Status:" in text:
                            status_text = text.replace("Status:", "").strip().lower()
                            details["status"] = "completed" if "finished" in status_text else "ongoing"
                        elif "Season:" in text:
                            details["release_date"] = text.replace("Season:", "").strip()
                            year_match = re.search(r'(\d{4})', details["release_date"])
                            if year_match:
                                details["release_date"] = f"{year_match.group(1)}-01-01"
                        elif "Studio:" in text or "Studios:" in text:
                            details["studio"] = text.replace("Studio:", "").replace("Studios:", "").strip()
                
                poster_elem = soup.select_one(".anime-poster img")
                if poster_elem:
                    details["cover_image"] = poster_elem.get("src")
                    
                bg_elem = soup.select_one(".anime-cover")
                if bg_elem:
                    style = bg_elem.get("style", "")
                    match = re.search(r'url\((.*?)\)', style)
                    if match:
                        bg_img = match.group(1).strip("'").strip('"')
                        if bg_img.startswith("//"):
                            bg_img = "https:" + bg_img
                        details["background_image"] = bg_img
                
                # Fetch Episodes Pagination via API
                episodes_data = []
                ep_page = 1
                retry_count = 0
                while True:
                    driver.get(f"{BASE_URL}/api?m=release&id={session}&sort=episode_asc&page={ep_page}")
                    time.sleep(2)
                    if "DDoS-Guard" in driver.page_source:
                        add_log("[*] DDoS-Guard challenge on episode API. Waiting 10 seconds...")
                        time.sleep(10)
                        
                    try:
                        wait.until(EC.presence_of_element_located((By.TAG_NAME, "pre")))
                        pre = driver.find_element(By.TAG_NAME, "pre").text
                        page_data = json.loads(pre)
                        if not page_data or "data" not in page_data or not page_data["data"]:
                            break
                        episodes_data.extend(page_data["data"])
                        if ep_page >= page_data["last_page"]:
                            break
                        ep_page += 1
                        retry_count = 0
                    except Exception as e:
                        retry_count += 1
                        if retry_count > 3:
                            add_log(f"[!] Too many retries on episode API for page {ep_page}. Exiting API loop.")
                            break
                        time.sleep(3)
                
                details["total_episodes"] = len(episodes_data)
                
                # Identify which episodes are actually new (missing locally)
                new_episodes_to_scrape = []
                for ep in episodes_data:
                    ep_num = ep["episode"]
                    if ep_num not in completed_episodes_dict:
                        new_episodes_to_scrape.append(ep)
                
                if not new_episodes_to_scrape:
                    add_log(f"[*] {title} is up-to-date for the new aired episodes. No new episodes found to scrape.")
                    # Keep local log updated
                    extraction_log[title] = {
                        "episodes_synced": len(completed_episodes_dict),
                        "total": details["total_episodes"],
                        "status": "completed" if len(completed_episodes_dict) >= details["total_episodes"] else "partial",
                        "last_update": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                    with open(EXTRACTION_LOG_FILE, "w", encoding="utf-8") as f:
                        json.dump(extraction_log, f, indent=4)
                    continue
                
                add_log(f"[*] Found {len(new_episodes_to_scrape)} new episodes to scrape for {title}.")
                
                new_scraped_details = []
                for ep in new_episodes_to_scrape:
                    ep_num = ep["episode"]
                    ep_url = f"{BASE_URL}/play/{session}/{ep['session']}"
                    
                    add_log(f"   -> Scraping resolutions for Episode {ep_num}...")
                    driver.get(ep_url)
                    time.sleep(2)
                    if "DDoS-Guard" in driver.page_source:
                        add_log("[*] DDoS-Guard challenge on play page. Waiting 10 seconds...")
                        time.sleep(10)
                    
                    ep_info = {"episode_number": ep_num, "resolutions": {}}
                    try:
                        wait.until(EC.presence_of_element_located((By.CSS_SELECTOR, "#resolutionMenu, #pickDownload, #video-embed, iframe.embed-responsive-item")))
                        time.sleep(1)
                        
                        buttons = driver.find_elements(By.CSS_SELECTOR, "#resolutionMenu button.dropdown-item")
                        if buttons:
                            for btn in buttons:
                                res_text = btn.get_attribute("textContent").strip()
                                match = re.search(r'(\d+p)', res_text)
                                res_text = match.group(1) if match else "default"
                                driver.execute_script("arguments[0].click();", btn)
                                try:
                                    wait.until(lambda d: "kwik" in d.find_element(By.CSS_SELECTOR, "iframe.embed-responsive-item").get_attribute("src"))
                                    src = driver.find_element(By.CSS_SELECTOR, "iframe.embed-responsive-item").get_attribute("src")
                                    if src:
                                        ep_info["resolutions"][res_text] = src
                                except: pass
                        else:
                            try:
                                iframe = driver.find_element(By.CSS_SELECTOR, "iframe.embed-responsive-item")
                                src = iframe.get_attribute("src")
                                if src:
                                    ep_info["resolutions"]["default"] = src
                            except: pass
                    except Exception as e:
                        add_log(f"[!] Error on play page: {e}")
                    
                    if ep_info["resolutions"]:
                        new_scraped_details.append(ep_info)
                        time.sleep(1.5)
                    else:
                        add_log(f"   [!] Failed to extract resolutions for Episode {ep_num}. Will retry next run.")
                
                if not new_scraped_details:
                    add_log("[!] No new episodes could be extracted. Skipping database write.")
                    continue
                
                # Merge new episodes into the main list and save local JSON
                details["episodes"] = list(completed_episodes_dict.values()) + new_scraped_details
                details["episodes"].sort(key=lambda x: x.get("episode_number", 0))
                
                save_to_json(details, json_filename)
                
                # Generate SQL for newly scraped episodes only
                sql_content = generate_sql(details, new_scraped_details)
                
                # Auto upload to database
                add_log(f"[*] Uploading SQL for {len(new_scraped_details)} new episodes...")
                success, err_msg = upload_sql_via_api(sql_content)
                
                if success:
                    add_log("[+] SQL successfully uploaded and executed.")
                    # Update progress log only on successful DB upload
                    new_synced_count = len(completed_episodes_dict) + len(new_scraped_details)
                    extraction_log[title] = {
                        "episodes_synced": new_synced_count,
                        "total": details["total_episodes"],
                        "status": "completed" if new_synced_count >= details["total_episodes"] else "partial",
                        "last_update": time.strftime("%Y-%m-%d %H:%M:%S")
                    }
                    with open(EXTRACTION_LOG_FILE, "w", encoding="utf-8") as f:
                        json.dump(extraction_log, f, indent=4)
                else:
                    add_log(f"[!] SQL Upload FAILED: {err_msg}")
                    # Remove from local file so we try it again next run
                    details["episodes"] = list(completed_episodes_dict.values())
                    save_to_json(details, json_filename)
                    
    except Exception as e:
        add_log(f"[FATAL] Scraper error: {e}")
        sys.exit(1)
    finally:
        if driver:
            try:
                driver.quit()
            except: pass
        add_log("[+] Scraper Finished.")

if __name__ == "__main__":
    main()
