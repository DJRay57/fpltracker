"""requests.get with retries, so one slow or 5xx API response doesn't fail a CI run."""
import time

import requests


def get(url, retries=4, **kwargs):
    for attempt in range(retries):
        try:
            r = requests.get(url, **kwargs)
            if r.status_code < 500 and r.status_code != 429:
                return r
        except (requests.ConnectionError, requests.Timeout):
            if attempt == retries - 1:
                raise
        else:
            if attempt == retries - 1:
                return r
        time.sleep(2 ** attempt * 2)
