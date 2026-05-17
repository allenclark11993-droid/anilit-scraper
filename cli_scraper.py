import os
import json
import time
import re
import sys
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
MAX_RUN_TIME = 20 * 60  # Stop scraping after 20 minutes to prevent timeouts and commit progress
START_TIME = time.time()

# --- UTILS ---
def add_log(text):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    print(f"{timestamp} {text}")

def slugify(text):
    if not text: return "n-a"
    text = text.lower()
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

    # Autodetect system Chromium (e.g. GitHub Actions / Linux)
    hf_chrome = "/usr/bin/chromium"
    hf_driver = "/usr/bin/chromedriver"
    
    if os.path.exists(hf_chrome):
        add_log("[*] System Chromium detected. Initializing headless browser.")
        options.binary_location = hf_chrome
        service = Service(hf_driver)
        return webdriver.Chrome(service=service, options=options)
    
    # Fallback to local setup (Windows/Mac)
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

# --- MAIN SCRAPER LOOP ---
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

        current_page = 527
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    status = json.load(f)
                    if "current_page" in status:
                        current_page = status["current_page"]
                        add_log(f"[*] Resuming progress from Page {current_page}")
            except: pass

        end_page = 1
        processed_sessions = set()
        
        while current_page >= end_page:
            # Check elapsed time
            elapsed = time.time() - START_TIME
            if elapsed >= MAX_RUN_TIME:
                add_log(f"[!] Reached maximum execution limit of {MAX_RUN_TIME} seconds. Stopping to save and commit progress.")
                break
                
            add_log(f"[*] Scanning Page {current_page}...")
            
            # Update status file
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "current_page": current_page, 
                    "timestamp": time.strftime("%Y-%m-%d %H:%M:%S")
                }, f, indent=4)

            driver.get(f"{BASE_URL}/?page={current_page}")
            time.sleep(2.5)
            
            try:
                items = driver.find_elements(By.CSS_SELECTOR, ".episode-title-wrap a")
                if not items:
                    add_log(f"[!] No items found on page {current_page}. Moving to next.")
                    current_page -= 1
                    continue
                    
                page_anime_list = []
                for item in items:
                    title = item.get_attribute("title")
                    href = item.get_attribute("href")
                    if title and href and "/anime/" in href:
                        session = href.split("/")[-1]
                        if session not in processed_sessions:
                            page_anime_list.append({"title": title, "session": session})
                            processed_sessions.add(session)
                
                if not page_anime_list:
                    current_page -= 1
                    continue

                page_anime_list.reverse() # Oldest first
                
                for i, anime in enumerate(page_anime_list):
                    # Check elapsed time before scraping each anime
                    if time.time() - START_TIME >= MAX_RUN_TIME:
                        break
                        
                    if anime['title'] in extraction_log:
                        log_entry = extraction_log[anime['title']]
                        if log_entry.get('status') == 'completed':
                            add_log(f"[*] Skipping: {anime['title']} (Already Scraped)")
                            continue
                            
                    add_log(f"[*] Scraping Anime ({i+1}/{len(page_anime_list)}): {anime['title']}")
                    
                    safe_name = re.sub(r'[^a-zA-Z0-9]', '_', anime['title'])
                    filename = os.path.join(DATA_DIR, f"{safe_name}.json")
                    
                    completed_episodes_dict = {}
                    if os.path.exists(filename):
                        try:
                            with open(filename, "r", encoding="utf-8") as f:
                                existing_data = json.load(f)
                                if "episodes" in existing_data:
                                    for e in existing_data["episodes"]:
                                        if e.get("resolutions"):
                                            completed_episodes_dict[e["episode_number"]] = e
                        except: pass
                            
                    # Load details
                    driver.get(f"{BASE_URL}/anime/{anime['session']}")
                    time.sleep(2.5)
                    
                    soup = BeautifulSoup(driver.page_source, "html.parser")
                    
                    details = {
                        "title": anime["title"],
                        "slug": slugify(anime["title"]),
                        "description": "N/A",
                        "genres": [],
                        "studio": "N/A",
                        "cover_image": "N/A",
                        "background_image": "N/A",
                        "release_date": "N/A",
                        "status": "ongoing",
                        "total_episodes": 0,
                        "episodes": []
                    }
                    
                    desc_elem = soup.select_one(".anime-synopsis")
                    if desc_elem: details["description"] = desc_elem.get_text(strip=True)
                        
                    genre_elems = soup.select(".anime-genre ul li a")
                    if genre_elems: details["genres"] = [g.get_text(strip=True) for g in genre_elems]
                        
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
                                if year_match: details["release_date"] = f"{year_match.group(1)}-01-01"
                            elif "Studio:" in text or "Studios:" in text:
                                details["studio"] = text.replace("Studio:", "").replace("Studios:", "").strip()
                    
                    poster_elem = soup.select_one(".anime-poster img")
                    if poster_elem: details["cover_image"] = poster_elem.get("src")
                        
                    bg_elem = soup.select_one(".anime-cover")
                    if bg_elem:
                        style = bg_elem.get("style", "")
                        match = re.search(r'url\((.*?)\)', style)
                        if match:
                            details["background_image"] = match.group(1).strip("'").strip('"')
                            if details["background_image"].startswith("//"):
                                details["background_image"] = "https:" + details["background_image"]
                            
                    # Fetch Episodes
                    episodes_data = []
                    ep_page = 1
                    retry_count = 0
                    while True:
                        if time.time() - START_TIME >= MAX_RUN_TIME:
                            break
                        driver.get(f"{BASE_URL}/api?m=release&id={anime['session']}&sort=episode_asc&page={ep_page}")
                        time.sleep(1.5)
                        
                        if "DDoS-Guard" in driver.page_source:
                            add_log("[!] DDoS-Guard challenge detected. Waiting 10s...")
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
                        except Exception as ex:
                            retry_count += 1
                            if retry_count > 3:
                                break
                            time.sleep(3)
                            
                    details["total_episodes"] = len(episodes_data)
                    
                    if len(completed_episodes_dict) >= len(episodes_data) and len(episodes_data) > 0:
                        add_log(f"[*] {anime['title']} already fully completed. Skipping sources.")
                        extraction_log[anime['title']] = {
                            "episodes_synced": len(episodes_data),
                            "total": len(episodes_data),
                            "status": "completed",
                            "last_update": time.strftime("%Y-%m-%d %H:%M:%S")
                        }
                        continue
                    
                    extracted_count = len(completed_episodes_dict)
                    for ep in episodes_data:
                        if time.time() - START_TIME >= MAX_RUN_TIME:
                            break
                            
                        ep_num = ep["episode"]
                        ep_url = f"{BASE_URL}/play/{anime['session']}/{ep['session']}"
                        
                        if ep_num in completed_episodes_dict:
                            continue
                            
                        add_log(f"   -> Extracting Ep {ep_num}...")
                        driver.get(ep_url)
                        
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
                                        if src: ep_info["resolutions"][res_text] = src
                                    except: pass
                            else:
                                try:
                                    iframe = driver.find_element(By.CSS_SELECTOR, "iframe.embed-responsive-item")
                                    src = iframe.get_attribute("src")
                                    if src: ep_info["resolutions"]["default"] = src
                                except: pass
                        except Exception as ee:
                            pass
                        
                        details["episodes"] = [ep_info]
                        save_to_json(details, filename)
                        extracted_count += 1
                        
                        extraction_log[anime['title']] = {
                            "episodes_synced": extracted_count,
                            "total": details["total_episodes"],
                            "status": "completed" if extracted_count >= details["total_episodes"] else "partial",
                            "last_update": time.strftime("%Y-%m-%d %H:%M:%S")
                        }
                        
                        with open(EXTRACTION_LOG_FILE, "w", encoding="utf-8") as f:
                            json.dump(extraction_log, f, indent=4)
                        
                        time.sleep(2.5)
                
                # Move to next page
                current_page -= 1
            except Exception as e:
                add_log(f"[!] Error on page {current_page}: {e}")
                time.sleep(5)
                
    except Exception as general_err:
        add_log(f"[FATAL] Scraper Exception: {general_err}")
    finally:
        if driver:
            try:
                driver.quit()
            except: pass
        add_log("[+] Scraper Finished & Closed.")

if __name__ == "__main__":
    main()
