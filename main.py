from MangaScraper import MangaScrapper
from time import sleep

scraper = MangaScrapper(timeout=60, workers=1)
MAX_PAGES = 100
for pg in range(1, MAX_PAGES + 1):
    scraper.process_page(pg)
    sleep(30)
