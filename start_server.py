import sys
sys.path.insert(0, '.')
from main import app
import uvicorn
uvicorn.run(app, host='127.0.0.1', port=8000, log_level='info')