from abc import ABC, abstractmethod


class BaseExtractor(ABC):

    def __init__(self, name):
        self.name = name

    @abstractmethod
    async def extract(self, lat, lng, start_date, end_date):
        pass

    @abstractmethod
    def parse(self, raw):
        pass

    def quality(self):
        return {
            "sensor": self.name,
            "confidence": "moderate",
            "limitations": [],
        }
