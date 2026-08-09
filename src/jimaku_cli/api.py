import requests

class JimakuClient:
  def __init__(self, api_key: str):
    self.api_key = api_key
    self.base_url = "https://jimaku.cc/"
