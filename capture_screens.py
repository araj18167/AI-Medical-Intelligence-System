"""Capture real screenshots of the running Medical Intelligence System
via headless Chrome + CDP for use in the PPTX/PDF documentation.
Outputs PNGs into ./docs_assets/
"""
import asyncio
import base64
import json
import subprocess
import time
import urllib.parse
import urllib.request

import websockets

CHROME = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
BASE = "http://127.0.0.1:3456"
API = "http://127.0.0.1:8000"
OUT = "docs_assets"
DEBUG_PORT = 9333


def api_login(username, password):
    data = urllib.parse.urlencode({"username": username, "password": password}).encode()
    req = urllib.request.Request(API + "/login", data=data, method="POST")
    req.add_header("Content-Type", "application/x-www-form-urlencoded")
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.load(r)["access_token"]


class Tab:
    def __init__(self, ws_url):
        self.ws_url = ws_url
        self._id = 0

    async def cmd(self, ws, method, params=None):
        self._id += 1
        await ws.send(json.dumps({"id": self._id, "method": method, "params": params or {}}))
        while True:
            m = json.loads(await ws.recv())
            if m.get("id") == self._id:
                if "error" in m:
                    raise RuntimeError(f"{method}: {m['error']}")
                return m.get("result", {})


async def shoot(tab_ws_url, nav_url, out_png, auth_token=None, settle=4.0, full=False):
    async with websockets.connect(tab_ws_url, max_size=64 * 1024 * 1024) as ws:
        tab = Tab(tab_ws_url)
        await tab.cmd(ws, "Page.enable")
        await tab.cmd(ws, "Runtime.enable")
        await tab.cmd(ws, "Emulation.setDeviceMetricsOverride",
                      {"width": 1440, "height": 900, "deviceScaleFactor": 1.5, "mobile": False})
        if auth_token:
            # land on origin first so localStorage is set for the right origin
            await tab.cmd(ws, "Page.navigate", {"url": BASE + "/login.html"})
            await asyncio.sleep(1.5)
            expr = f'localStorage.setItem("access_token", {json.dumps(auth_token)}); "ok"'
            await tab.cmd(ws, "Runtime.evaluate", {"expression": expr})
        await tab.cmd(ws, "Page.navigate", {"url": nav_url})
        await asyncio.sleep(settle)
        params = {"format": "png", "captureBeyondViewport": bool(full)}
        shot = await tab.cmd(ws, "Page.captureScreenshot", params)
        with open(out_png, "wb") as f:
            f.write(base64.b64decode(shot["data"]))
        print("saved", out_png)


async def main():
    tokens = {
        "patient": api_login("test.patient.1", "P@ss1234"),
        "doctor": api_login("dr1", "Test@1234"),
        "nurse": api_login("nurse1", "Nurse@1234"),
        "hospital": api_login("bhawani1234@gmail.com", "Hosp@1234"),
        "shop": api_login("bhawani5061@gmail.com", "Test@1234"),
    }
    print("logins ok")

    jobs = [
        # (outfile, url, token_key or None, settle, fullpage)
        ("01_landing.png",        BASE + "/index.html",                    None,       5.0, False),
        ("02_login.png",          BASE + "/login.html",                    None,       4.0, False),
        ("03_patient_dash.png",   BASE + "/dashboard.html",                "patient",  6.0, False),
        ("04_doctor_dash.png",    BASE + "/dashboard.html",                "doctor",   6.0, False),
        ("05_nurse_dash.png",     BASE + "/nurse-dashboard.html",          "nurse",    6.0, False),
        ("06_hospital_dash.png",  BASE + "/hospital-dashboard.html",       "hospital", 6.0, False),
        ("07_pharmacy_dash.png",  BASE + "/dashboard.html",                "shop",     6.0, False),
        ("08_ai_reports.png",     BASE + "/patient-ai-reports.html",       "patient",  6.0, False),
        ("09_billings.png",       BASE + "/patient-billings.html",         "patient",  6.0, False),
        ("10_mediecho.png",       BASE + "/mediecho.html",                 "patient",  6.0, False),
        ("11_hosp_billing.png",   BASE + "/hospital-billing.html",         "hospital", 6.0, False),
        ("12_inventory.png",      BASE + "/inventory.html",                "shop",     6.0, False),
    ]

    for out, url, tok, settle, full in jobs:
        # fresh tab per shot
        req = urllib.request.Request(f"http://127.0.0.1:{DEBUG_PORT}/json/new?about:blank", method="PUT")
        with urllib.request.urlopen(req, timeout=10) as r:
            info = json.load(r)
        ws_url = info["webSocketDebuggerUrl"]
        try:
            await shoot(ws_url, url, f"{OUT}/{out}",
                        auth_token=tokens[tok] if tok else None,
                        settle=settle, full=full)
        except Exception as e:
            print("FAILED", out, e)
        finally:
            try:
                urllib.request.urlopen(
                    f"http://127.0.0.1:{DEBUG_PORT}/json/close/{info['id']}", timeout=5)
            except Exception:
                pass


if __name__ == "__main__":
    import os
    os.makedirs(OUT, exist_ok=True)
    chrome = subprocess.Popen([
        CHROME, "--headless=new", f"--remote-debugging-port={DEBUG_PORT}",
        "--user-data-dir=" + os.path.abspath(".chrome_tmp"),
        "--no-first-run", "--no-default-browser-check", "--disable-gpu",
        "--window-size=1440,900", "--hide-scrollbars", "about:blank",
    ])
    time.sleep(4)
    try:
        asyncio.run(main())
    finally:
        chrome.terminate()
        print("done")
