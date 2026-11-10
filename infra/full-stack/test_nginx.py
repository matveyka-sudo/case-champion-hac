"""Opt-in checks against the pinned Nginx image and real deployment config."""

import gzip
import http.client
import json
import os
import subprocess
import tempfile
import time
import unittest
from pathlib import Path
from uuid import uuid4

IMAGE = os.environ.get("WAYDEX_NGINX_TEST_IMAGE", "")
RESULT_PATH = "/api/v1/analyses/7ac40d85-e7cf-42a0-98e0-88ea20c2bc28"


@unittest.skipUnless(IMAGE, "Set WAYDEX_NGINX_TEST_IMAGE to run Docker proxy checks")
class NginxResponseCompressionTests(unittest.TestCase):
    @classmethod
    def docker(cls, *args: str) -> str:
        return subprocess.check_output(
            ["docker", *args], text=True, stderr=subprocess.STDOUT
        ).strip()

    @classmethod
    def setUpClass(cls) -> None:
        cls.directory = tempfile.TemporaryDirectory(prefix="waydex-nginx-")
        cls.folder = Path(cls.directory.name)
        # The upstream worker runs as nginx, not the host user that owns mkdtemp.
        # These synthetic public fixtures must remain readable on Linux binds.
        cls.folder.chmod(0o755)
        cls.network = "waydex-nginx-" + uuid4().hex[:12]
        cls.backend = cls.network + "-api"
        cls.proxy = cls.network + "-proxy"
        cls.addClassCleanup(cls.directory.cleanup)
        cls.addClassCleanup(cls.cleanup_containers)
        cls.payload = json.dumps({"evidence": ["saved public source " * 12] * 1024}).encode()
        (cls.folder / "payload.json").write_bytes(cls.payload)
        cls.post_payload = json.dumps({"response": "private response " * 120}).encode()
        (cls.folder / "upstream.conf").write_text(
            "server {\n"
            " listen 8000;\n"
            " default_type application/json;\n"
            " add_header Cache-Control no-store always;\n"
            " add_header Vary Origin always;\n"
            ' add_header Set-Cookie "a2_session=fixture; HttpOnly; SameSite=Strict; Path=/" always;\n'
            " add_header X-Received-Cookie $http_cookie always;\n"
            " add_header X-Received-Origin $http_origin always;\n"
            " add_header X-Received-Host $http_host always;\n"
            " location / {\n"
            "  if ($request_method = POST) { return 200 '" + cls.post_payload.decode() + "'; }\n"
            "  root /fixtures;\n"
            "  try_files /payload.json =404;\n"
            " }\n"
            "}\n"
        )
        for fixture in ("payload.json", "upstream.conf"):
            (cls.folder / fixture).chmod(0o644)
        cls.docker("network", "create", cls.network)
        common = ("--network", cls.network, "--memory", "64m", "--pids-limit", "64")
        cls.docker(
            "run",
            "-d",
            "--name",
            cls.backend,
            "--network-alias",
            "api",
            *common,
            "--mount",
            f"type=bind,source={cls.folder},target=/fixtures,readonly",
            "--mount",
            f"type=bind,source={cls.folder / 'upstream.conf'},target=/etc/nginx/conf.d/default.conf,readonly",
            IMAGE,
        )
        cls.docker(
            "run",
            "-d",
            "--name",
            cls.proxy,
            *common,
            "-p",
            "127.0.0.1::8080",
            "--mount",
            f"type=bind,source={Path(__file__).with_name('nginx.conf').resolve()},target=/etc/nginx/conf.d/default.conf,readonly",
            IMAGE,
        )
        cls.docker("exec", cls.proxy, "nginx", "-t")
        inspect = json.loads(cls.docker("inspect", cls.proxy))[0]
        cls.port = int(inspect["NetworkSettings"]["Ports"]["8080/tcp"][0]["HostPort"])
        deadline = time.monotonic() + 10
        while True:
            try:
                status, _, _ = cls.request(RESULT_PATH)
                if status == 200:
                    break
            except (OSError, http.client.HTTPException):
                pass
            if time.monotonic() >= deadline:
                raise AssertionError(
                    "Isolated Nginx proxy did not become ready\n" + cls.readiness_diagnostics()
                )
            time.sleep(0.1)

    @classmethod
    def readiness_diagnostics(cls) -> str:
        details = []
        for container in (cls.backend, cls.proxy):
            try:
                state = json.loads(cls.docker("inspect", container))[0]["State"]
                details.append(container + " state=" + json.dumps(state))
                details.append(cls.docker("logs", "--tail", "20", container))
            except subprocess.CalledProcessError as error:
                details.append(container + ": " + error.output)
        return "\n".join(details)

    @classmethod
    def cleanup_containers(cls) -> None:
        for name in (cls.proxy, cls.backend):
            subprocess.run(["docker", "rm", "-f", name], capture_output=True, check=False)
        subprocess.run(["docker", "network", "rm", cls.network], capture_output=True, check=False)

    @classmethod
    def request(
        cls, path: str, method: str = "GET", encoding: str = "gzip", via: bool = False
    ) -> tuple[int, dict[str, str], bytes]:
        connection = http.client.HTTPConnection("127.0.0.1", cls.port, timeout=5)
        headers = {
            "Accept-Encoding": encoding,
            "Cookie": "a2_session=client-session",
            "Origin": "https://waydex.example:443",
            "Host": "waydex.example:443",
        }
        if via:
            headers["Via"] = "1.1 test-proxy"
        try:
            connection.request(method, path, headers=headers)
            response = connection.getresponse()
            received = dict(response.getheaders())
            received["Vary"] = ", ".join(response.headers.get_all("Vary", []))
            return response.status, received, response.read()
        finally:
            connection.close()

    def test_saved_result_compresses_losslessly_and_preserves_session_headers(self) -> None:
        for via in (False, True):
            with self.subTest(via=via):
                status, headers, body = self.request(RESULT_PATH + "?download=1", via=via)
                self.assertEqual(status, 200)
                self.assertEqual(headers.get("Content-Encoding"), "gzip")
                self.assertEqual(gzip.decompress(body), self.payload)
                self.assertLess(len(body), len(self.payload) // 4)
                self.assertEqual(headers.get("Cache-Control"), "no-store")
                self.assertIn("Accept-Encoding", headers.get("Vary", ""))
                self.assertIn("Origin", headers.get("Vary", ""))
                self.assertEqual(headers.get("X-Received-Cookie"), "a2_session=client-session")
                self.assertEqual(headers.get("X-Received-Origin"), "https://waydex.example:443")
                self.assertEqual(headers.get("X-Received-Host"), "waydex.example:443")
                self.assertEqual(
                    headers.get("Set-Cookie"),
                    "a2_session=fixture; HttpOnly; SameSite=Strict; Path=/",
                )

    def test_identity_client_receives_original_result(self) -> None:
        status, headers, body = self.request(RESULT_PATH, encoding="identity")
        self.assertEqual(status, 200)
        self.assertNotIn("Content-Encoding", headers)
        self.assertEqual(body, self.payload)
        self.assertEqual(headers.get("Cache-Control"), "no-store")

    def test_other_routes_and_methods_remain_uncompressed(self) -> None:
        cases = [
            ("POST", RESULT_PATH),
            ("GET", RESULT_PATH + "/explanation"),
            ("POST", RESULT_PATH + "/explanation"),
            ("GET", RESULT_PATH + "/scenarios"),
            ("GET", "/api/v1/auth/session"),
            ("GET", "/api/v1/session"),
            ("POST", "/api/v1/token"),
            ("POST", "/api/v1/analyses"),
        ]
        for method, path in cases:
            with self.subTest(method=method, path=path):
                status, headers, body = self.request(path, method=method)
                self.assertEqual(status, 200)
                self.assertNotIn("Content-Encoding", headers)
                self.assertEqual(body, self.post_payload if method == "POST" else self.payload)
                self.assertEqual(headers.get("Cache-Control"), "no-store")


if __name__ == "__main__":
    unittest.main()
