from dataclasses import dataclass

@dataclass
class Manga:
    title: str
    url: str
    cover_url: str
    page: int

@dataclass
class Chapter:
    number: int
    title: str
    url: str