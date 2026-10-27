"""Pretend Ollama for testing node.py on a laptop with no GPU. Answers '42' after a model-sized delay."""
import json
import re
import time
from http.server import BaseHTTPRequestHandler, HTTPServer


class Handler(BaseHTTPRequestHandler):
    def do_POST(self):
        body = json.loads(self.rfile.read(int(self.headers["content-length"])))
        size = float((re.search(r"(\d+(?:\.\d+)?)b", body["model"]) or [0, 1])[1])
        time.sleep(0.4 * size)
        content = json.dumps({"reasoning": f"mock {body['model']}", "answer": "42 apples"})
        out = json.dumps({"message": {"role": "assistant", "content": content}}).encode()
        self.send_response(200)
        self.send_header("content-type", "application/json")
        self.end_headers()
        self.wfile.write(out)

    def log_message(self, *args):
        pass


HTTPServer(("127.0.0.1", 11434), Handler).serve_forever()
