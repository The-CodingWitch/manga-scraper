import json
import logging
import os
import re
from time import sleep
from typing import List
from dataclasses import asdict
from urllib.parse import urljoin, urlparse
from concurrent.futures import ThreadPoolExecutor, as_completed
from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from selenium.webdriver.chrome.service import Service
from selenium.webdriver.common.by import By
from selenium.webdriver.support.ui import WebDriverWait
from selenium.webdriver.support import expected_conditions as EC
from selenium.common.exceptions import TimeoutException, WebDriverException
from webdriver_manager.chrome import ChromeDriverManager
from bs4 import BeautifulSoup
from models import DataModels


class MangaScrapper:
    def __init__(self, timeout: int, workers: int):
        self.base_url = "https://m440.in"
        self.filter_path = "/filterList"
        self.load_page_timeout = timeout
        logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s: %(message)s")
        self.logger = logging.getLogger("manga-scraper")
        self.max_workers = workers
        self.json_page = "manga_page_{page}.json"
        self.directory = "output/mangas"

    def init_list_driver(self) -> webdriver.Chrome:
        opts = Options()
        opts.add_argument("--headless=new")
        opts.add_argument("--disable-gnu")
        opts.add_argument("--no-sandbox")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
        driver.set_page_load_timeout(self.load_page_timeout)
        return driver

    def init_chapter_driver(self) -> webdriver.Chrome:
        opts = Options()
        opts.add_argument("--disable-gpu")
        opts.add_argument("--no-sandbox")
        driver = webdriver.Chrome(service=Service(ChromeDriverManager().install()), options=opts)
        driver.set_page_load_timeout(self.load_page_timeout)
        return driver

    def fetch_page(self, driver: webdriver.Chrome, page: int) -> List[DataModels.Manga]:
        url = f"{self.base_url}{self.filter_path}?page={page}&cat=25&alpha=&sortBy=name&asc=true&author=&tag=&artist="
        self.logger.info(f"[page: {page}] Loading list - {url}")
        try:
            driver.get(url)
            WebDriverWait(driver, 10).until(
                EC.presence_of_all_elements_located((By.CSS_SELECTOR, "div.media-left"))
            )
        except(TimeoutException, WebDriverException):
            self.logger.warning(f"[Page {page}] list load issue")
        soup = BeautifulSoup(driver.page_source, "html.parser")
        out: List[DataModels.Manga] = []
        for div in soup.select("div.media-left"):
            a, img = div.find("a", href=True), div.find("img", src=True)
            if not (a and img): continue
            out.append(
                DataModels.Manga(
                    title=img.get("title", img.get("alt", "")).strip(),
                    url=urljoin(self.base_url, a["href"]),
                    cover_url=img["src"],
                    page=page
                )
            )
        self.logger.info(f"[Page {page}] found {len(out)} manga")
        return out

    def fetch_chapters(self, manga: DataModels.Manga) -> dict:
        path = urlparse(manga.url).path.rstrip("/")
        chapter_pattern = re.compile(re.escape(path) + r"/(\d+)(?:-([^\"\'<>]+))?")
        driver = self.init_chapter_driver()
        try:
            driver.get(manga.url)
            WebDriverWait(driver, 15).until(lambda d: d.execute_script("return document.readyState") == "complete")
            sleep(1)
            html = driver.page_source
        except (TimeoutException, WebDriverException):
            self.logger.warning(f"    [{manga.title}] chapter page load failed")
            url, page = manga.url, manga.page
            driver.quit()
            return {"title": manga.title, "image": manga.cover_url, "url": url, "page": page, "chapters": []}
        driver.quit()

        seen = set()
        chapters: List[DataModels.Chapter] = []

        for match in chapter_pattern.findall(html):
            if isinstance(match, tuple):
                chap_num, chap_id = match
            else:
                chap_num, chap_id = match, None
            num_i = int(chap_num)
            base_href = f"{self.base_url}{path}/{chap_num}"
            href = f"{base_href}-{chap_id}" if chap_id else base_href
            if href in seen:
                continue
            seen.add(href)
            em = re.search(
                re.escape(f'href="{href}"') + r'[^>]*>\s*<em>(.*?)</em>',
                html, re.DOTALL
            )
            title = em.group(1).strip() if em else f"Chapter {num_i}"
            chapters.append(
                DataModels.Chapter(
                    number=num_i,
                    title=title,
                    url=href
                )
            )

        if not chapters:
            soup = BeautifulSoup(html, "html.parser")
            for a in soup.select("ul.sPZrewc a[target='_blank']"):
                href = a["href"]
                seg = urlparse(href).path.rstrip("/").split("/")[-1]
                num_i = int(seg.split("-", 1)[0])
                url = href if href.startswith("http") else urljoin(self.base_url, href)
                if url in seen:
                    continue
                seen.add(url)
                title = a.find("em").get_text(strip=True) if a.find("em") else f"Chapter {num_i}"
                chapters.append(
                    DataModels.Chapter(
                        number=num_i, title=title, url=url
                    )
                )

        chapters.sort(key=lambda c: c.number)
        return {
            "title": manga.title,
            "image": manga.cover_url,
            "url": manga.url,
            "page": manga.page,
            "chapters": [asdict(chapter) for chapter in chapters]
        }

    def process_page(self, page: int):
        driver = self.init_list_driver()
        try:
            mangas = self.fetch_page(driver, page)
        finally:
            driver.quit()

        results = []
        with ThreadPoolExecutor(max_workers=self.max_workers) as exe:
            futures = {exe.submit(self.fetch_chapters, manga): manga for manga in mangas}
            for fut in as_completed(futures):
                data = fut.result()
                if data["chapters"]:
                    results.append(data)
        try:
            os.makedirs(self.directory, exist_ok=True)
            self.logger.info(f"[{self.directory}] created directory")
        except OSError as e:
            self.logger.warning(f"Error creating directory {self.directory} {e.strerror}")


        file_path = os.path.join(self.directory, self.json_page.format(page=page))
        with open(file_path, "w", encoding="utf-8") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)
        self.logger.info(f"[Page {page}] wrote {len(results)} entries to {file_path}")