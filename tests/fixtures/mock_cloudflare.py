#!/usr/bin/env python3
import json
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse


STATE = {"records": [], "configs": []}


def json_response(handler, payload, status=200):
    body = json.dumps(payload).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json")
    handler.send_header("Content-Length", str(len(body)))
    handler.end_headers()
    handler.wfile.write(body)


def next_id():
    return f"record-{len(STATE['records']) + 1}"


class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/dump":
            return json_response(self, STATE)
        if parsed.path.startswith("/client/v4/zones/") and parsed.path.endswith("/dns_records"):
            zone_id = parsed.path.split("/")[4]
        else:
            return json_response(self, {"success": False, "errors": ["not-found"]}, 404)
        query = parse_qs(parsed.query)
        record_type = query.get("type", [""])[0]
        name = query.get("name", [""])[0]
        matches = [
            record for record in STATE["records"]
            if record["zone_id"] == zone_id and record["type"] == record_type and record["name"] == name
        ]
        return json_response(self, {"success": True, "result": matches})

    def do_POST(self):
        if not (self.path.startswith("/client/v4/zones/") and self.path.endswith("/dns_records")):
            return json_response(self, {"success": False, "errors": ["not-found"]}, 404)
        zone_id = self.path.split("/")[4]
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        payload["id"] = next_id()
        payload["zone_id"] = zone_id
        STATE["records"].append(payload)
        return json_response(self, {"success": True, "result": payload}, 201)

    def do_PUT(self):
        length = int(self.headers.get("Content-Length", "0"))
        payload = json.loads(self.rfile.read(length))
        if "/dns_records/" in self.path and self.path.startswith("/client/v4/zones/"):
            zone_id = self.path.split("/")[4]
            record_id = self.path.rsplit("/", 1)[1]
            for index, record in enumerate(STATE["records"]):
                if record["id"] == record_id and record["zone_id"] == zone_id:
                    payload["id"] = record_id
                    payload["zone_id"] = zone_id
                    STATE["records"][index] = payload
                    return json_response(self, {"success": True, "result": payload})
            return json_response(self, {"success": False, "errors": ["missing-record"]}, 404)
        if self.path.startswith("/client/v4/accounts/") and "/cfd_tunnel/" in self.path and self.path.endswith("/configurations"):
            account_id = self.path.split("/")[4]
            tunnel_id = self.path.split("/")[6]
            STATE["configs"].append({"account_id": account_id, "tunnel_id": tunnel_id, "payload": payload})
            return json_response(self, {"success": True, "result": {"version": len(STATE["configs"])}})
        return json_response(self, {"success": False, "errors": ["missing-record"]}, 404)

    def log_message(self, *_args):
        return


def main():
    if len(sys.argv) != 2:
        raise SystemExit("usage: mock_cloudflare.py <port>")
    port = int(sys.argv[1])
    server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
    print(port, flush=True)
    server.serve_forever()


if __name__ == "__main__":
    main()
