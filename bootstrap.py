import hashlib
import io
import json
import os
import shutil
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile


def main():
    os.umask(0o077)
    for name in ("SFT_ARCHIVE_KEY_B64", "SFT_ARCHIVE_HOSTS_B64"):
        parts = []
        while f"{name}_{len(parts):02d}" in os.environ:
            parts.append(os.environ.pop(f"{name}_{len(parts):02d}"))
        if parts:
            os.environ[name] = "".join(parts)
    key = os.environ.pop("BUNDLE_KEY")
    payload = (Path(__file__).resolve().parent / "payload.fernet").read_bytes()
    with tempfile.TemporaryDirectory(prefix="monarch-qwen-bundle-") as directory:
        root = Path(directory)
        dependencies = root / "dependencies"
        subprocess.run([
            sys.executable, "-m", "pip", "install", "--disable-pip-version-check",
            "--no-cache-dir", "--only-binary=:all:", "--target", str(dependencies),
            "cryptography==46.0.5",
        ], check=True)
        sys.path.insert(0, str(dependencies))
        from cryptography.fernet import Fernet

        archive_bytes = Fernet(key.encode()).decrypt(payload)
        del key
        source = root / "source"
        source.mkdir()
        with tarfile.open(fileobj=io.BytesIO(archive_bytes), mode="r:gz") as archive:
            archive.extractall(source, filter="data")
        manifest = json.loads((source / "bundle_manifest.json").read_text())
        for row in manifest["files"]:
            actual = hashlib.sha256((source / row["path"]).read_bytes()).hexdigest()
            if actual != row["sha256"]:
                raise RuntimeError("Bundle file checksum mismatch")
        print("BUNDLE_AUTHENTICATED sha256=" + hashlib.sha256(payload).hexdigest(),
              flush=True)
        if sys.argv[1:] == ["--verify-only"]:
            return 0
        if sys.argv[1:] == ["--preflight"]:
            import torch
            volumes = {}
            for name in ("/home/jovyan", "/workspace-SR006.nfs2", "/workspace-SR006.nfs3", "/tmp"):
                if Path(name).exists():
                    usage = shutil.disk_usage(name)
                    volumes[name] = {"total": usage.total, "free": usage.free}
            print("SFT_PREFLIGHT=" + json.dumps({"torch": torch.__version__, "volumes": volumes}), flush=True)
            if "SFT_ARCHIVE_KEY_B64" in os.environ:
                subprocess.run([sys.executable, str(source / "sft_archive.py"), "--probe"], check=True)
            return 0
        environment = dict(os.environ)
        environment["PROBE_SOURCE_COMMIT"] = manifest["source_commit"]
        return subprocess.run(["bash", manifest.get("entry", "scripts/cloud_probe_transfer.sh"), *sys.argv[1:]],
                              cwd=source, env=environment).returncode


if __name__ == "__main__":
    raise SystemExit(main())
