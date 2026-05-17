import streamlit as st
import os
import json
import time
import re
import io
import zipfile
import threading
from bs4 import BeautifulSoup
from selenium import webdriver
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from webdriver_manager.chrome import ChromeDriverManager

# --- PAGE SETUP ---
st.set_page_config(
    page_title="AniLit Scraper Hub",
    page_icon="🚀",
    layout="wide",
    initial_sidebar_state="expanded"
)

# --- DIRECTORIES & PATHS ---
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.path.join(BASE_DIR, "extracted_data")
STATUS_FILE = os.path.join(BASE_DIR, "current_status.json")
LOG_FILE = os.path.join(BASE_DIR, "scraper_run.log")
EXTRACTION_LOG_FILE = os.path.join(BASE_DIR, "extraction_log.json")

os.makedirs(DATA_DIR, exist_ok=True)

# --- UTILS ---
def slugify(text):
    if not text: return "n-a"
    text = text.lower()
    text = re.sub(r'[^a-z0-9]+', '-', text)
    return text.strip('-')

def add_log(text):
    timestamp = time.strftime("[%Y-%m-%d %H:%M:%S]")
    log_line = f"{timestamp} {text}\n"
    with open(LOG_FILE, "a", encoding="utf-8") as f:
        f.write(log_line)

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

    # Hugging Face Spaces / Linux default installation paths
    hf_chrome = "/usr/bin/chromium"
    hf_driver = "/usr/bin/chromedriver"
    
    if os.path.exists(hf_chrome):
        add_log("[*] Hugging Face environment detected. Using system Chromium.")
        options.binary_location = hf_chrome
        service = Service(hf_driver)
        return webdriver.Chrome(service=service, options=options)
    
    # Fallback to local setup (Windows/Mac)
    add_log("[*] Local environment detected. Initializing Chrome via WebDriverManager.")
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

# --- GLOBAL SCRAPER THREAD MANAGER ---
class ScraperManager:
    def __init__(self):
        self.thread = None
        self.stop_event = threading.Event()
        self.is_running = False
        self.current_page = 527
        self.current_anime = "None"

@st.cache_resource
def get_scraper_manager():
    return ScraperManager()

manager = get_scraper_manager()

# --- BACKROUND SCRAPING THREAD TASK ---
def run_scraper_task(start_page, page_delay, stop_event):
    manager.is_running = True
    add_log("[+] Scraper Thread Started Successfully.")
    
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

        current_page = start_page
        if os.path.exists(STATUS_FILE):
            try:
                with open(STATUS_FILE, "r", encoding="utf-8") as f:
                    status = json.load(f)
                    if "current_page" in status:
                        current_page = status["current_page"]
                        add_log(f"[*] Resuming from Page {current_page}")
            except: pass

        end_page = 1
        processed_sessions = set()
        
        while current_page >= end_page:
            if stop_event.is_set():
                add_log("[!] Stop signal received. Halting scraper execution.")
                break
                
            manager.current_page = current_page
            add_log(f"[*] Scanning Page {current_page}...")
            
            # Save status
            with open(STATUS_FILE, "w", encoding="utf-8") as f:
                json.dump({"current_page": current_page, "timestamp": time.time()}, f, indent=4)

            driver.get(f"{BASE_URL}/?page={current_page}")
            time.sleep(page_delay)
            
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
                    if stop_event.is_set():
                        break
                        
                    manager.current_anime = anime['title']
                    
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
                    time.sleep(page_delay)
                    
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
                        if stop_event.is_set():
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
                        if stop_event.is_set():
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
                        
                        time.sleep(page_delay)
                
                # Move to next page
                current_page -= 1
            except Exception as e:
                add_log(f"[!] Error on page {current_page}: {e}")
                time.sleep(5)
                
    except Exception as general_err:
        add_log(f"[FATAL] Scraper Thread Exception: {general_err}")
    finally:
        if driver:
            try:
                driver.quit()
            except: pass
        manager.is_running = False
        add_log("[+] Scraper Thread Finished & Terminated.")

# --- ZIP UTILITY ---
def get_zip_of_extracted_data():
    zip_buffer = io.BytesIO()
    with zipfile.ZipFile(zip_buffer, "a", zipfile.ZIP_DEFLATED, False) as zip_file:
        for root, dirs, files in os.walk(DATA_DIR):
            for file in files:
                file_path = os.path.join(root, file)
                zip_file.write(file_path, os.path.relpath(file_path, DATA_DIR))
    return zip_buffer.getvalue()

# --- STREAMLIT UI DESIGN ---

st.title("🚀 AniLit Autonomous Scraper Hub")
st.markdown("A premium serverless web console running headless Selenium on **Hugging Face Spaces**.")
st.write("---")

# Load extraction log
total_scraped_animes = 0
if os.path.exists(EXTRACTION_LOG_FILE):
    try:
        with open(EXTRACTION_LOG_FILE, "r") as f:
            log_data = json.load(f)
            total_scraped_animes = len(log_data)
    except: pass

total_json_files = len([f for f in os.listdir(DATA_DIR) if f.endswith(".json")])

# Row 1: Metrics
m1, m2, m3, m4 = st.columns(4)

with m1:
    if manager.is_running:
        st.metric("Scraper Engine Status", "Active 🟢", delta="Running scraper thread")
    else:
        st.metric("Scraper Engine Status", "Idle 💤", delta="Waiting for trigger")

with m2:
    st.metric("Current Scraping Page", f"Page {manager.current_page}", delta="Crawling from 527 -> 1")

with m3:
    st.metric("Scraped Anime Catalog", f"{total_scraped_animes} Animes", delta="In extraction log")

with m4:
    st.metric("Total JSON Files", f"{total_json_files} Files", delta="Stored in local storage")

st.write("---")

# Row 2: Layout Columns
col_controls, col_monitor = st.columns([1, 2])

with col_controls:
    st.subheader("⚙️ Control Dashboard")
    
    # Inputs
    start_page_input = st.number_input("Target Starting Page", min_value=1, max_value=1000, value=int(manager.current_page))
    delay_input = st.slider("Request Delay Interval (seconds)", min_value=1.0, max_value=10.0, value=2.5, step=0.5)
    
    st.write("### Actions")
    
    # Start and Stop Buttons
    btn_start, btn_stop = st.columns(2)
    
    with btn_start:
        if st.button("🟢 Start / Resume", use_container_width=True, disabled=manager.is_running):
            manager.stop_event.clear()
            manager.thread = threading.Thread(
                target=run_scraper_task, 
                args=(start_page_input, delay_input, manager.stop_event),
                daemon=True
            )
            manager.thread.start()
            st.toast("Background scraping thread started!")
            st.rerun()
            
    with btn_stop:
        if st.button("🛑 Pause / Stop", use_container_width=True, disabled=not manager.is_running):
            manager.stop_event.set()
            st.toast("Stopping scraper gracefully...")
            st.rerun()
            
    # Reset Button
    st.write("---")
    st.write("### Danger Zone")
    if st.button("🔄 Hard Reset Progress", type="primary", use_container_width=True):
        # Soft reset variables
        manager.current_page = 527
        manager.current_anime = "None"
        # Delete status files
        for fpath in [STATUS_FILE, LOG_FILE, EXTRACTION_LOG_FILE]:
            if os.path.exists(fpath):
                try:
                    os.remove(fpath)
                except: pass
        add_log("[!] Scraper hard reset executed by user.")
        st.success("All status files cleared! Scraper state reset to default Page 527.")
        time.sleep(1)
        st.rerun()

    # Download Data
    st.write("---")
    st.subheader("📦 Exporter Center")
    if total_json_files > 0:
        st.write(f"Compress and download all `{total_json_files}` extracted JSON files.")
        zip_data = get_zip_of_extracted_data()
        st.download_button(
            label="📥 Download all JSON files as ZIP",
            data=zip_data,
            file_name="extracted_anime_data.zip",
            mime="application/zip",
            use_container_width=True
        )
    else:
        st.info("No JSON data files extracted yet. Run the scraper to generate files.")

with col_monitor:
    st.subheader("🖥️ Live Stream Log Monitor")
    
    # Status Details
    st.markdown(f"**Currently Scraping Anime:** `{manager.current_anime}`")
    
    # Log Viewer
    log_content = ""
    if os.path.exists(LOG_FILE):
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                lines = f.readlines()
                log_content = "".join(lines[-50:]) # Display last 50 lines
        except:
            log_content = "Reading logs..."
    else:
        log_content = "No logs generated yet. Click 'Start / Resume' to begin."
        
    st.code(log_content, language="bash")
    
    # Refresh Indicator
    if manager.is_running:
        st.write("🔄 *Auto-refreshing dashboard logs every 2 seconds...*")
        time.sleep(2)
        st.rerun()
    else:
        st.write("⏸️ *Engine is idle. Auto-refresh paused. Click actions to run.*")
        if st.button("🔄 Force Refresh Logs", use_container_width=True):
            st.rerun()
