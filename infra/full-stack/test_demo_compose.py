"""Check demo project isolation with Compose configuration and no containers."""

import json
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

WRAPPER = Path(__file__).resolve().with_name("demo-compose.sh")
PROJECT = "waydex-demo-isolation-test"
PORT = "5297"


class DemoComposeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        cls.docker = shutil.which("docker")
        if (
            cls.docker is None
            or subprocess.run([cls.docker, "compose", "version"], capture_output=True).returncode
        ):
            raise unittest.SkipTest("Docker Compose CLI is required for config checks")

    def setUp(self) -> None:
        folder = tempfile.TemporaryDirectory(prefix="waydex-demo-test-")
        self.addCleanup(folder.cleanup)
        self.directory = Path(folder.name)
        self.env_file = self.directory / "private demo.env"
        self.settings = {
            "COMPOSE_PROJECT_NAME": f'"{PROJECT}" # demo project',
            "DEMO_PORT_FIXTURE": PORT,
            "WAYDEX_HTTP_PORT": '"${DEMO_PORT_FIXTURE}" # demo port',
            "WAYDEX_REVISION": "fixture-revision",
            "WAYDEX_SOURCE_DIR": f'"{self.directory}/source directory"',
            "WAYDEX_DEPLOYMENT_DIR": f'"{self.directory}/demo runtime"',
            "WAYDEX_PRODUCT_MANIFEST": "manifest.json",
            "WAYDEX_PRODUCT_SHA256": "0" * 64,
            "POSTGRES_PASSWORD": "config-fixture-only",
            "WAYDEX_ASSISTANT_ENV_FILE": "/dev/null",
        }
        ambient_assistant = self.directory / "ambient-assistant.env"
        ambient_assistant.write_text("A2_AMBIENT_ASSISTANT=loaded\n")
        self.env = {
            **os.environ,
            "COMPOSE_PROJECT_NAME": "waydex-real",
            "WAYDEX_HTTP_PORT": "5190",
            "WAYDEX_REVISION": "ambient-revision",
            "WAYDEX_ASSISTANT_ENV_FILE": str(ambient_assistant),
        }

    def demo(self, *args: str, inline_env_arg: bool = False) -> subprocess.CompletedProcess[str]:
        self.env_file.write_text(
            "".join(f"{key}={value}\n" for key, value in self.settings.items())
        )
        env_args = (
            [f"--env-file={self.env_file.name}"]
            if inline_env_arg
            else ["--env-file", self.env_file.name]
        )
        return subprocess.run(
            [str(WRAPPER), *env_args, *args],
            cwd=self.directory,
            env=self.env,
            capture_output=True,
            text=True,
        )

    def assert_demo_config(self, result: subprocess.CompletedProcess[str]) -> None:
        self.assertEqual(result.returncode, 0, result.stderr)
        project = json.loads(result.stdout)
        (port,) = project["services"]["web"]["ports"]
        self.assertEqual(
            (project["name"], port["host_ip"], str(port["published"])),
            (PROJECT, "127.0.0.1", PORT),
        )
        self.assertEqual(project["volumes"]["postgres_data"]["name"], f"{PROJECT}_postgres_data")
        self.assertEqual(project["volumes"]["observations"]["name"], f"{PROJECT}_observations")
        api = project["services"]["api"]
        self.assertEqual(api["image"], "waydex-full-api:fixture-revision")
        self.assertNotIn("A2_AMBIENT_ASSISTANT", api["environment"])

    def test_config_uses_private_identity_despite_shell_values(self) -> None:
        self.assert_demo_config(self.demo("config", "--format", "json", inline_env_arg=True))

    def test_identity_interpolation_uses_aliases_from_the_private_file(self) -> None:
        self.settings = {"DEMO_PROJECT_FIXTURE": PROJECT, **self.settings}
        self.settings["COMPOSE_PROJECT_NAME"] = '"${DEMO_PROJECT_FIXTURE}"'
        self.env["DEMO_PROJECT_FIXTURE"] = "waydex-real"
        self.env["DEMO_PORT_FIXTURE"] = "5190"
        self.assert_demo_config(self.demo("config", "--format", "json"))

    def test_down_gets_the_same_explicit_identity(self) -> None:
        # Intercept down and render the same model without contacting the daemon.
        shim = self.directory / "docker"
        shim.write_text(
            f"""#!/usr/bin/env python3
import json
import os
import sys
from pathlib import Path

args = sys.argv[1:]
if args[-1:] == ["down"]:
    Path("down-arguments.json").write_text(json.dumps(args))
    args[-1:] = ["config", "--format", "json"]
if args[:1] != ["compose"] or args[-3:] != ["config", "--format", "json"]:
    sys.exit("unexpected Docker invocation")
docker = {self.docker!r}
os.execv(docker, [docker, *args])
"""
        )
        shim.chmod(0o755)
        self.env["PATH"] = f"{self.directory}{os.pathsep}{self.env['PATH']}"
        self.assert_demo_config(self.demo("down"))
        args = json.loads((self.directory / "down-arguments.json").read_text())
        self.assertEqual(args[args.index("--project-name") + 1], PROJECT)

    def test_missing_identity_is_rejected_even_when_exported(self) -> None:
        for key in ("COMPOSE_PROJECT_NAME", "WAYDEX_HTTP_PORT"):
            with self.subTest(key=key):
                value = self.settings.pop(key)
                result = self.demo("config", "--quiet")
                self.settings[key] = value
                self.assertNotEqual(result.returncode, 0)
                self.assertIn(key, result.stderr)

    def test_invalid_ports_are_rejected(self) -> None:
        for port in ("0", "65536", "5297-5298"):
            with self.subTest(port=port):
                self.settings["WAYDEX_HTTP_PORT"] = port
                result = self.demo("config", "--quiet")
                self.assertNotEqual(result.returncode, 0)
                self.assertIn("WAYDEX_HTTP_PORT", result.stderr)

    def test_compose_global_flag_cannot_replace_project(self) -> None:
        result = self.demo("--project-name", "waydex-real", "config", "--quiet")
        self.assertNotEqual(result.returncode, 0)


if __name__ == "__main__":
    unittest.main()
