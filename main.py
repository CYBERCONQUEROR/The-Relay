"""The Relay API. Run uvicorn main:app --host 127.0.0.1 --port 8000."""
import logging
from dotenv import load_dotenv

load_dotenv()
logging.basicConfig(level=logging.INFO, format='%(message)s')

from reliability.api import create_app

app = create_app()
