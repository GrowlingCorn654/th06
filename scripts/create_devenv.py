from argparse import ArgumentParser, Namespace
from datetime import datetime
import hashlib
import os
import os.path
from pathlib import Path
import shutil
import subprocess
import sys

try:
    from typing import Optional
except ImportError:
    pass
import urllib.request

from winhelpers import run_windows_program, get_windows_path

SCRIPTS_DIR = Path(__file__).parent


def run_msiextract(msi_file_path: Path, output_dir: Path) -> int:
    return subprocess.check_call(
        ["msiextract", str(msi_file_path)], cwd=str(output_dir)
    )


def cmd_quote(s):
    if " " not in s:
        return s
    return '"' + s.replace("\\", "\\\\").replace('"', '"') + '"'


def run_msiextract_win32(msi_file_path: Path, output_dir: Path) -> int:
    return subprocess.check_call(
        "msiexec /a "
        + cmd_quote(str(msi_file_path))
        + " /qb TARGETDIR="
        + cmd_quote(str(output_dir)),
        cwd=str(output_dir),
        # We need to use shell=True as msiexec does some very funky parsing of the command line arguments.
        shell=True,
    )


def translate_msiextract_name(raw_name: str) -> "Optional[str]":
    name = raw_name.split(":")[0]

    if name == ".":
        return None

    return name


def msiextract(msi_file_path: Path, output_dir: Path) -> int:
    os.makedirs(str(output_dir), exist_ok=True)
    if sys.platform == "win32":
        run_msiextract_win32(msi_file_path, output_dir)

        return

    run_msiextract(msi_file_path, output_dir)

    for dir in output_dir.glob("**/.:*"):
        parent_dir = dir.parent

        for entry in dir.glob("*"):
            new_entry = parent_dir / entry.name
            print(f"Renaming {entry} -> {new_entry}")
            if not new_entry.exists():
                shutil.move(str(entry), str(new_entry))
            else:
                copytree_exist_ok(str(entry), str(new_entry))
                shutil.rmtree(str(entry))

        dir.rmdir()

    should_continue = True
    while should_continue:
        renamed_something = False

        for entry in output_dir.glob("**"):
            if entry.is_dir():
                new_name = translate_msiextract_name(entry.name)

                if new_name is None:
                    continue

                new_entry = entry.parent / new_name

                if entry != new_entry:
                    print(f"Renaming {entry} -> {new_entry}")
                    if not new_entry.exists():
                        shutil.move(str(entry), str(new_entry))
                        renamed_something = True
                        break
                    else:
                        copytree_exist_ok(str(entry), str(new_entry))
                        shutil.rmtree(str(entry))
                        renamed_something = True
                        break

        should_continue = renamed_something


def copytree_exist_ok(src: Path, dst: Path):
    if sys.version_info >= (3, 8):
        shutil.copytree(src, dst, dirs_exist_ok=True)
    else:
        import distutils.dir_util

        distutils.dir_util.copy_tree(str(src), str(dst))


def check_file(path: Path, message: str) -> Path:
    if not path.exists():
        sys.stderr.write(message + "\n")
        sys.exit(1)

    return path.absolute()


def parse_arguments() -> Namespace:
    parser = ArgumentParser(description="Prepare devenv")
    parser.add_argument(
        "--only",
        action="append",
        choices=["vs", "dx8", "py", "pragma", "cygwin", "ninja"],
        help="Only run certain steps. Possible values are vs, dx8, py, pragma, cygwin and ninja.",
    )
    parser.add_argument("dl_cache_path", help="Path to download the requirements in")
    parser.add_argument("output_path", help="The output directory")
    parser.add_argument(
        "--download",
        action="store_true",
        help="Only download the components, don't install them.",
    )

    return parser.parse_args()


def get_sha256(path):
    h = hashlib.new("sha256")
    with path.open("rb") as f:
        while True:
            data = f.read(16 * 4096 * 4096)
            if not data:
                break
            h.update(data)
    return h.hexdigest()


units = {"GB": 1024**3, "MB": 1024**2, "KiB": 1024**1}


def parse_size(size):
    for unit, val in units.items():
        if size > val:
            return format(float(size) / val, ".2f") + unit

    return str(size) + "B"


clear_line_sequence = "" if sys.platform == "win32" else "\033[2K"

last_refresh = datetime.now()


def progress_bar(blocks_transfered, block_size, total_bytes):
    global last_refresh

    if (datetime.now() - last_refresh).total_seconds() < 1:
        return

    # Progress bar size
    size = 80

    bytes_transfered = blocks_transfered * block_size

    x = int(size * bytes_transfered / total_bytes)
    clear_line_sequence = "" if sys.platform == "win32" else "\033[2K"
    print(
        "{}[{}{}] {}/{}".format(
            clear_line_sequence,
            "#" * x,
            "." * (size - x),
            parse_size(bytes_transfered),
            parse_size(total_bytes),
        ),
        end="\r",
        file=sys.stdout,
        flush=True,
    )
    last_refresh = datetime.now()


def download_requirement(dl_cache_path, requirement):
    path = dl_cache_path / requirement["filename"]
    if path.exists() and get_sha256(path) == requirement["sha256"]:
        return

    print("Downloading " + requirement["name"])
    urllib.request.urlretrieve(requirement["url"], str(path), progress_bar)
    print(clear_line_sequence, end="", flush=True, file=sys.stdout)
    hash = get_sha256(path)
    if hash != requirement["sha256"]:
        raise Exception(
            "Download failed: Got hash " + hash + ", expected " + requirement["sha256"]
        )
def download_requirement_torrent(dl_cache_path, requirement, aria2c_path):
    path = dl_cache_path / requirement["filename"]
    if path.exists() and get_sha256(path) == requirement["sha256"]:
        return

    print("Downloading " + requirement["name"] + " using torrent")
    # Run aria2c to download the torrent, make sure to save only the file we want.
    subprocess.check_call(str(aria2c_path) + " --dir " + str(dl_cache_path) + " --summary-interval=0 --seed-time=0 --select-file=4 " + str(requirement["torrent"]), shell=True)
    # After downloading, take the target file in the torrent_directory and move it back to the root of the dl_cache_path
    shutil.move(str(dl_cache_path / requirement["torrent_dirname"] / requirement["filename"]), str(path))
    print(clear_line_sequence, end="", flush=True, file=sys.stdout)
    hash = get_sha256(path)
    if hash != requirement["sha256"]:
        raise Exception("Download failed: Got hash " + hash + ", expected " + requirement["sha256"])
    os.removedirs(str(dl_cache_path / requirement["torrent_dirname"]))


def download_requirements(dl_cache_path, steps):
    requirements = [
        {
            "name": "Direct X 8.0",
            "only": "dx8",
            "url": "https://archive.org/download/dx8sdk/dx8sdk.exe",
            "filename": "dx8sdk.exe",
            "sha256": "719f8fe4f02af5f435aac4a90bf9ef958210e6bd1d1e9715f26d13b10a73cb6c",
        },
        {
            "name": "Visual Studio .NET 2002 Professional Edition",
            "only": "vs",
            "url": "https://archive.org/download/en_vs.net_pro_full/en_vs.net_pro_full.exe",
            "torrent": "https://archive.org/download/en_vs.net_pro_full/en_vs.net_pro_full_archive.torrent",
            "filename": "en_vs.net_pro_full.exe",
            "torrent_dirname": "en_vs.net_pro_full",
            "sha256": "440949f3d152ee0375050c2961fc3c94786780b5aae7f6a861a5837e03bf2dac",
        },
        {
            "name": "Python 3.4.4",
            "only": "py",
            "url": "https://www.python.org/ftp/python/3.4.4/python-3.4.4.msi",
            "filename": "python-3.4.4.msi",
            "sha256": "46c8f9f63cf02987e8bf23934b2f471e1868b24748c5bb551efcf4863b43ca6c",
        },
        {
            "name": "WiRunSQL",
            "only": "py",
            "url": "https://raw.githubusercontent.com/microsoft/Windows-classic-samples/44d192fd7ec6f2422b7d023891c5f805ada2c811/Samples/Win7Samples/sysmgmt/msi/scripts/WiRunSQL.vbs",
            "filename": "WiRunSQL.vbs",
            "sha256": "ef18c6d0b0163e371daaa1dd3fdf08030bc0b0999e4b2b90a1a736f7eb12784b",
        },
        {
            "name": "Cygwin",
            "only": "cygwin",
            # On darwin, for whatever reason, the 32-bit installer fails. Let's
            # just grab the 64-bit installer instead.
            "condition": sys.platform == "darwin",
            "url": "http://ctm.crouchingtigerhiddenfruitbat.org/pub/cygwin/setup/snapshots/setup-x86_64-2.874.exe",
            "filename": "cygwin-setup-2.874.exe",
            "sha256": "58f9f42f5dbd52c5e3ecd24e537603ee8897ea15176b7acdc34afcef83e5c19a",
        },
        {
            "name": "Cygwin",
            "only": "cygwin",
            "condition": sys.platform != "darwin",
            "url": "http://ctm.crouchingtigerhiddenfruitbat.org/pub/cygwin/setup/snapshots/setup-x86-2.874.exe",
            "filename": "cygwin-setup-2.874.exe",
            "sha256": "a79e4f57ce98a4d4bacb8fbb66fcea3de92ef30b34ab8b76e11c8bd3b426fd31",
        },
        {
            "name": "Ninja",
            "only": "ninja",
            "url": "https://github.com/ninja-build/ninja/releases/download/v1.6.0/ninja-win.zip",
            "filename": "ninja-win.zip",
            "sha256": "18f55bc5de27c20092e86ace8ef3dd3311662dc6193157e3b65c6bc94ce006d5",
        },
    ]

    useTorrents = input("Would you like to download using torrents? (y/n) ")
    if useTorrents.lower() == "y":
        # Download aria2c
        if sys.platform == "win32":
            aria2c_path = dl_cache_path / "aria2c.exe"
            if not aria2c_path.exists():
                print("Downloading aria2c")
                urllib.request.urlretrieve("https://github.com/aria2/aria2/releases/download/release-1.37.0/aria2-1.37.0-win-64bit-build1.zip", str(dl_cache_path / "aria2.zip"))
                shutil.unpack_archive(str(dl_cache_path / "aria2.zip"), str(dl_cache_path), format="zip")
                os.remove(str(dl_cache_path / "aria2.zip"))
                # Move aria2c to the correct location
                shutil.move(str(dl_cache_path / "aria2-1.37.0-win-64bit-build1" / "aria2c.exe"), str(aria2c_path))
                shutil.rmtree(str(dl_cache_path / "aria2-1.37.0-win-64bit-build1"), ignore_errors=True)
        else:
            # assuming its already in their PATH, because it should be installed before selecting torrent downloads on linux.
            aria2c_path = "aria2c"
            if not shutil.which(aria2c_path):
                # throw an error if aria2c is not installed
                raise Exception("aria2c is not installed, please install it before selecting torrent downloads!")
        for requirement in requirements:
            if "torrent" in requirement:
                download_requirement_torrent(dl_cache_path, requirement, aria2c_path)

    for requirement in requirements:
        if requirement["only"] in steps:
            if "condition" not in requirement or requirement["condition"]:
                download_requirement(dl_cache_path, requirement)


def install_compiler_sdk(installer_path, tmp_dir, tmp2_dir, output_path):
    print("Installing Compiler and Platform SDK")
    compiler_directories = [
        "PROGRAM FILES/MICROSOFT VISUAL STUDIO .NET/COMMON7/IDE",
        "PROGRAM FILES/MICROSOFT VISUAL STUDIO .NET/VC7/BIN",
        "PROGRAM FILES/MICROSOFT VISUAL STUDIO .NET/VC7/INCLUDE",
        "PROGRAM FILES/MICROSOFT VISUAL STUDIO .NET/VC7/LIB",
    ]

    sdk_directories = ["Program Files/Microsoft Visual Studio .NET/Vc7/PlatformSDK"]
    shutil.rmtree(str(tmp_dir), ignore_errors=True)
    os.makedirs(str(tmp_dir), exist_ok=True)
    shutil.unpack_archive(str(installer_path), str(tmp_dir), format="zip")

    for compiler_directory_part in compiler_directories:
        dst_required_directory_path = output_path / compiler_directory_part
        src_required_directory_path = tmp_dir / compiler_directory_part
        copytree_exist_ok(src_required_directory_path, dst_required_directory_path)

    msvcr70_dll_src_path = tmp_dir / "MSVCR70.DLL"
    shutil.copy(
        str(msvcr70_dll_src_path),
        str(output_path / "PROGRAM FILES/MICROSOFT VISUAL STUDIO .NET/VC7/BIN"),
    )

    # Extract and grab Windows SDK
    os.makedirs(str(tmp2_dir), exist_ok=True)
    msiextract(tmp_dir / "VS_SETUP.MSI", tmp2_dir)
    shutil.rmtree(str(tmp_dir), ignore_errors=True)

    for sdk_directory_part in sdk_directories:
        dst_required_directory_path = output_path / sdk_directory_part
        src_required_directory_path = tmp2_dir / sdk_directory_part
        copytree_exist_ok(src_required_directory_path, dst_required_directory_path)

    shutil.rmtree(str(tmp2_dir), ignore_errors=True)

    # Uniformalize everything
    should_continue = True
    while should_continue:
        renamed_something = False

        for entry in output_path.glob("**"):
            new_name = entry.name.upper()

            if new_name == entry.name or entry == output_path:
                continue

            new_entry = entry.parent / new_name

            if (
                entry.exists()
                and new_entry.exists()
                and os.path.samefile(str(entry), str(new_entry))
            ):
                continue

            if entry.is_file():
                shutil.copy(str(entry), str(new_entry))
                entry.unlink()
            else:
                copytree_exist_ok(entry, new_entry)
                shutil.rmtree(str(entry))

            renamed_something = True
            break

        should_continue = renamed_something


def install_directx8(dx8sdk_installer_path, tmp_dir, output_path):
    print("Installing DirectX 8.0 SDK")
    shutil.rmtree(str(tmp_dir), ignore_errors=True)
    os.makedirs(str(tmp_dir), exist_ok=True)
    shutil.unpack_archive(str(dx8sdk_installer_path), str(tmp_dir), format="zip")
    dx8sdk_dst_dir = output_path / "mssdk"
    shutil.rmtree(str(dx8sdk_dst_dir), ignore_errors=True)
    shutil.move(str(tmp_dir), str(dx8sdk_dst_dir))
    shutil.rmtree(str(tmp_dir), ignore_errors=True)


def install_python(python_installer_path, wirunsql_path, tmp_dir, output_path):
    print("Installing Python")
    shutil.rmtree(str(tmp_dir), ignore_errors=True)
    os.makedirs(str(tmp_dir), exist_ok=True)
    shutil.copyfile(str(python_installer_path), str(tmp_dir / "python.msi"))

    # On windows, make sure we extract the msvcrt100.dll properly
    if sys.platform == "win32":
        run_windows_program(
            [
                "cscript",
                str(wirunsql_path),
                str(tmp_dir / "python.msi"),
                "UPDATE Feature SET Level=1 WHERE Feature='PrivateCRT'",
            ]
        )

    os.makedirs(str(tmp_dir / "python"), exist_ok=True)
    msiextract(tmp_dir / "python.msi", tmp_dir / "python")
    python_dst_dir = output_path / "python"
    shutil.rmtree(str(python_dst_dir), ignore_errors=True)
    shutil.move(str(tmp_dir / "python"), str(python_dst_dir))
    shutil.rmtree(str(tmp_dir), ignore_errors=True)


def install_cygwin(cygwin_installer_path, tmp_dir, output_path):
    print("Installing cygwin")
    os.makedirs(str(tmp_dir), exist_ok=True)
    local_package_dir_win32 = get_windows_path(tmp_dir / "cygwin_cache")
    cygwin_dir_win32 = get_windows_path(output_path / "cygwin")
    run_windows_program(
        [
            str(cygwin_installer_path),
            "--quiet-mode",
            "--only-site",
            "--site",
            "http://ctm.crouchingtigerhiddenfruitbat.org/pub/cygwin/circa/2002/11/12/084110",
            "--no-verify",
            "--root",
            cygwin_dir_win32,
            "--local-package-dir",
            local_package_dir_win32,
            "--no-shortcuts",
            "--no-startmenu",
            "--no-desktop",
            "--arch",
            "x86",
            "--packages",
            "gcc",
        ],
        cwd=str(tmp_dir),
    )
    shutil.rmtree(str(tmp_dir), ignore_errors=True)


def install_pragma_var_order(tmp_dir, output_path):
    print("Installing pragma_var_order")
    os.makedirs(str(tmp_dir), exist_ok=True)
    win32_path_to_pragma_var_order = get_windows_path(
        SCRIPTS_DIR / "pragma_var_order.cpp"
    )
    run_windows_program(
        [
            str(SCRIPTS_DIR / "th06run.bat"),
            "CL.EXE",
            win32_path_to_pragma_var_order,
            "/o" + str(tmp_dir / "hackery.dll"),
            "/link",
            "/DLL",
        ],
        add_env={"DEVENV_PREFIX": str(output_path)},
    )
    VC7 = output_path / "PROGRAM FILES/MICROSOFT VISUAL STUDIO .NET/VC7"
    if not (VC7 / "BIN/C1XXOrig.DLL").exists():
        shutil.move(str(VC7 / "BIN/C1XX.DLL"), str(VC7 / "BIN/C1XXOrig.DLL"))
    shutil.move(str(tmp_dir / "hackery.dll"), str(VC7 / "BIN/C1XX.DLL"))
    shutil.rmtree(str(tmp_dir), ignore_errors=True)


def install_ninja(ninja_zip_path, output_path):
    print("Installing ninja")
    install_path = output_path / "ninja"
    os.makedirs(str(install_path), exist_ok=True)
    shutil.unpack_archive(str(ninja_zip_path), str(install_path))


def main(args: Namespace) -> int:
    dl_cache_path = Path(args.dl_cache_path).absolute()
    output_path = Path(args.output_path).absolute()

    tmp_dir = output_path / "tmp"
    tmp2_dir = output_path / "tmp2"

    if args.only is None or len(args.only) == 0:
        steps = set(["vs", "dx8", "py", "pragma", "cygwin", "ninja"])
    else:
        steps = set(args.only)

    os.makedirs(str(dl_cache_path), exist_ok=True)
    download_requirements(dl_cache_path, steps)

    if not args.download:
        program_files = output_path / "PROGRAM FILES"
        os.makedirs(str(program_files), exist_ok=True)

        dx8sdk_installer_path = dl_cache_path / "dx8sdk.exe"
        installer_path = dl_cache_path / "en_vs.net_pro_full.exe"
        python_installer_path = dl_cache_path / "python-3.4.4.msi"
        wirunsql_path = dl_cache_path / "WiRunSQL.vbs"
        cygwin_installer_path = dl_cache_path / "cygwin-setup-2.874.exe"
        ninja_zip_path = dl_cache_path / "ninja-win.zip"

        if "vs" in steps:
            install_compiler_sdk(installer_path, tmp_dir, tmp2_dir, output_path)
        if "dx8" in steps:
            install_directx8(dx8sdk_installer_path, tmp_dir, output_path)
        if "py" in steps:
            install_python(python_installer_path, wirunsql_path, tmp_dir, output_path)
        if "pragma" in steps:
            install_pragma_var_order(tmp_dir, output_path)
        if "cygwin" in steps:
            install_cygwin(cygwin_installer_path, tmp_dir, output_path)
        if "ninja" in steps:
            install_ninja(ninja_zip_path, output_path)

    return 0


if __name__ == "__main__":
    sys.exit(main(parse_arguments()))
