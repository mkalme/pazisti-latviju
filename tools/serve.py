#!/usr/bin/env python3
"""Static server for the game with caching disabled, so code/data changes
always show up on plain reload."""
import sys
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path
import os

os.chdir(Path(__file__).resolve().parent.parent)
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8747


class Handler(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-cache")
        super().end_headers()

    def log_message(self, *args):
        pass


print(f"serving on http://localhost:{PORT}")
HTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
