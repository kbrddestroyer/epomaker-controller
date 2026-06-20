import os
import subprocess
from pathlib import Path


def test_upload_helper_restarts_active_service(tmp_path: Path) -> None:
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    log_file = tmp_path / "commands.log"
    image_file = tmp_path / "image.png"
    image_file.write_bytes(b"test")

    systemctl = bin_dir / "systemctl"
    systemctl.write_text(
        "#!/usr/bin/env bash\n"
        "echo systemctl \"$@\" >> \"$HELPER_LOG\"\n"
        "if [[ \"$1\" == \"is-active\" ]]; then exit 0; fi\n"
    )
    systemctl.chmod(0o755)

    controller = bin_dir / "epomakercontroller"
    controller.write_text(
        "#!/usr/bin/env bash\n"
        "echo epomakercontroller \"$@\" >> \"$HELPER_LOG\"\n"
    )
    controller.chmod(0o755)

    env = os.environ.copy()
    env["PATH"] = f"{bin_dir}:{env['PATH']}"
    env["HELPER_LOG"] = str(log_file)

    subprocess.run(
        ["service/epomaker-upload-image", str(image_file)],
        cwd=Path(__file__).parents[1],
        env=env,
        check=True,
    )

    assert log_file.read_text().splitlines() == [
        "systemctl is-active --quiet epomaker-controller.service",
        "systemctl stop epomaker-controller.service",
        f"epomakercontroller upload-image {image_file}",
        "systemctl start epomaker-controller.service",
    ]
