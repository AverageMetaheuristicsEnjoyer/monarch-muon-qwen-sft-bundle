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
        if sys.argv[1:] == ["--prepare-restart"]:
            run_root = Path("/home/jovyan/monarch-qwen-sft-20260929")
            config = json.loads((source / "sft_config.json").read_text())
            smoke = json.loads((run_root / "smoke-g2-recovery/complete.json").read_text())
            expected = "027e8810a99e738e636ac0fc1cdffdc4340ef5d92be13ea1e6ff618002f4ec4b"
            if len(smoke) != 4 or {(r["structure"], r["optimizer"]) for r in smoke} != {
                (s, a) for s in ("dense", "monarch") for a in ("adamw", "muon")
            }:
                raise RuntimeError("Incomplete recovery smoke")
            for result in smoke:
                if result["identity"]["config"] != config or result["identity"]["projection_sha256"] != expected:
                    raise RuntimeError("Recovery smoke configuration mismatch")
            receipt = smoke[-1]["archive"]
            if receipt["sha256"] != "ef3eea6a7c1556457938a6d039e9b8947be6a992a40944257663321580931f75" or receipt["bytes"] != 10518995488:
                raise RuntimeError("Recovery checkpoint archive mismatch")
            for name in ("/home/jovyan", "/workspace-SR006.nfs3"):
                free = shutil.disk_usage(name).free
                print("SFT_RESTART_FREE=" + json.dumps({"path": name, "free": free}), flush=True)
                if free < 2 * 1024**3:
                    raise RuntimeError("Insufficient persistent storage")
            backup = run_root / "before-recovery-20260929"
            backup.mkdir(exist_ok=False)
            runs = run_root / "runs-g2"
            if runs.exists():
                runs.rename(backup / runs.name)
            for path in run_root.glob("trial-*-g2-rank*.log"):
                path.rename(backup / path.name)
            print("SFT_RESTART_READY=" + json.dumps({"preserved": str(backup), "projection_sha256": expected}), flush=True)
            return 0
        if sys.argv[1:] == ["--reclaim-initialization"]:
            run_root = Path("/home/jovyan/monarch-qwen-sft-20260929")
            projection = run_root / "monarch-initial.safetensors"
            expected = "027e8810a99e738e636ac0fc1cdffdc4340ef5d92be13ea1e6ff618002f4ec4b"
            receipt = json.loads((run_root / "archives/monarch-initial.json").read_text())
            if receipt["sha256"] != expected or receipt["bytes"] != 10518995488:
                raise RuntimeError("Initialization archive receipt mismatch")
            if projection.exists():
                sys.path.insert(0, str(source))
                from sft_archive import file_sha256
                if projection.stat().st_size != receipt["bytes"] or file_sha256(projection) != expected:
                    raise RuntimeError("Initialization duplicate checksum mismatch")
                projection.unlink()
                print("SFT_RECLAIMED_BYTES=" + str(receipt["bytes"]), flush=True)
            else:
                print("SFT_INITIALIZATION_DUPLICATE_ALREADY_ABSENT", flush=True)
            for name in ("/home/jovyan", "/workspace-SR006.nfs2", "/workspace-SR006.nfs3"):
                print("SFT_DISK=" + json.dumps({"path": name, "usage": shutil.disk_usage(name)._asdict()}), flush=True)
            return 0
        if sys.argv[1:] == ["--archive-initialization"]:
            sys.path.insert(0, str(source))
            from sft_archive import upload
            run_root = Path("/home/jovyan/monarch-qwen-sft-20260929")
            projection = run_root / "monarch-initial.safetensors"
            receipt = upload(projection)
            upload(projection.with_suffix(".json"))
            (run_root / "archives/monarch-initial.json").write_text(json.dumps(receipt, indent=2) + "\n")
            print("SFT_INITIALIZATION_ARCHIVED=" + json.dumps(receipt), flush=True)
            return 0
        if sys.argv[1:] == ["--inspect-sft"]:
            run_root = Path("/home/jovyan/monarch-qwen-sft-20260929")
            for name in ("/home/jovyan", "/workspace-SR006.nfs2", "/workspace-SR006.nfs3", "/tmp"):
                if Path(name).exists():
                    print("SFT_DISK=" + json.dumps({"path": name, "usage": shutil.disk_usage(name)._asdict()}), flush=True)
            for name in ("/home/jovyan", str(run_root)):
                result = subprocess.run(["du", "-x", "-B1", "--max-depth=1", name], capture_output=True, text=True, timeout=120)
                print("SFT_DISK_DIRECTORIES " + name + "\n" + result.stdout, flush=True)
            print("SFT_ROOT_EXISTS=" + str(run_root.exists()), flush=True)
            if run_root.exists():
                print(json.dumps({p.name: p.stat().st_size for p in run_root.iterdir()}), flush=True)
                for path in sorted(run_root.glob("runs-g2/*/*.json")):
                    print("SFT_RESULT " + str(path) + " " + path.read_text(), flush=True)
                for path in sorted(run_root.glob("*.log")):
                    print("SFT_LOG " + path.name, flush=True)
                    with path.open(errors="replace") as stream:
                        from collections import deque
                        print("".join(deque(stream, maxlen=30)), flush=True)
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
